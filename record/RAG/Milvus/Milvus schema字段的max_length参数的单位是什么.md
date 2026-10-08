```python
async def _create_collection(self):
    client = self.client
    schema = client.create_schema(
        auto_id=False,
        enable_dynamic_field=True
    )
    # 该id要自己生成
    schema.add_field("id", DataType.VARCHAR, is_primary=True, max_length=64)
    schema.add_field("dense_vector", DataType.FLOAT_VECTOR, dim=1024)
    schema.add_field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR)
    analyzer_params = {
        "tokenizer": "standard",
        "filter": ["lowercase"]
    }
    schema.add_field("chunk_text", DataType.VARCHAR, max_length=4096, enable_analyzer=True, analyzer_params = analyzer_params, enable_match = True)
    # 添加doc_id，用于快速查找文档对应的chunk
    schema.add_field("doc_id", DataType.VARCHAR, max_length=64)
    # 添加scope，如course, user
    schema.add_field("scope", DataType.VARCHAR, max_length=64)
    # 添加scope_id，用于进行各种过滤
    schema.add_field("scope_id", DataType.VARCHAR, max_length=64)
    # Add function to schema
    bm25_function = Function(
        name="text_bm25_emb",
        input_field_names=["chunk_text"],
        output_field_names=["sparse_vector"],
        function_type=FunctionType.BM25,
    )
    schema.add_function(bm25_function)
    # 为字段创建索引
    index_params = client.prepare_index_params()
    index_params.add_index(field_name="dense_vector", index_type="AUTOINDEX", metric_type="IP")
    index_params.add_index(field_name="sparse_vector", index_type="SPARSE_INVERTED_INDEX", metric_type="BM25",params={"inverted_index_algo": "DAAT_MAXSCORE"})
    index_params.add_index(field_name="chunk_text", index_type="AUTOINDEX")
    # 标量字段建立倒排索引（加速 scope 过滤与按文档删除）
    index_params.add_index(field_name="scope_id", index_name="idx_scope_id", index_type="INVERTED")
    index_params.add_index(field_name="doc_id", index_name="idx_doc_id", index_type="INVERTED")
    index_params.add_index(field_name="scope", index_name="idx_scope", index_type="INVERTED")
    await client.create_collection(
        collection_name=self.collection_name,
        schema=schema,
        index_params=index_params
    )
    if (await client.has_collection(self.collection_name)):
        print("Collection created successfully")
    else:
        print("Failed to create collection")
```

### 问：这是milvus创建schema的代码，我的主要疑问在于max_length的单位是什么，对于汉字、英文字母有区别吗？

在 Milvus 中，`DataType.VARCHAR` 的 `max_length` 单位是 **字节（Byte）**，而不是字符数。



因为 Milvus 采用 UTF-8 编码存储字符串，所以**汉字和英文字符占用的长度有很大区别**。



**字符与字节对应关系**



- **英文与 ASCII 字符**（字母、数字、英文标点）：1 个字符 = 1 字节。
- **常用汉字**（中文常用字、中文标点）：1 个汉字 = **3 字节**。
- **Emoji 及部分生僻字**：1 个字符 = **4 字节**。

**代码中字段容量换算**



| **字段名**                             | **max_length (字节)** | **纯英文容量** | **纯中文容量（按 3 字节/字）** |
| -------------------------------------- | --------------------- | -------------- | ------------------------------ |
| `id` / `doc_id` / `scope` / `scope_id` | 64                    | 64 个字符      | 约 **21 个汉字**               |
| `chunk_text`                           | 4096                  | 4096 个字符    | 约 **1365 个汉字**             |

**实践建议**



- **`chunk_text` 长度控制**：在 RAG 场景中，如果文本切分分块（Chunk Size）设置在 500~1000 字左右，4096 字节完全够用；但若分块较大（如包含 Markdown 表格、HTML 标签、代码块等），建议将 `max_length` 调大（Milvus VARCHAR 单字段最大支持 **65,535 字节**）。
- **超出限制的行为**：插入数据时，如果实际字符串的 **UTF-8 字节长度** 超过了 `max_length`，Milvus 会直接抛出异常导致插入失败，因此在前端切分或清洗数据时需以 `len(text.encode('utf-8'))` 为准进行校验。

### 问：我主要就是担心插入文本块后长度超了报错，所以为了防止这个报错，还得用len(text.encode('utf-8'))来校验是吗？另外你说的对，确实将 max_length 调大才稳妥，而文本块长度的控制交给对应的splitter控制即可。就是不知道max_length调的很大会影响性能啥的吗

**校验方式**



是的，如果要做严格的前端拦截或截断，必须使用 `len(text.encode('utf-8'))`。因为 Python 的 `len(text)` 统计的是**字符数**，遇到中文、Emoji 或特殊符号时，字节数会成倍增加（例如 1000 个汉字在 Python 里 `len()` 是 1000，但编码后实际是 3000 字节）。



如果希望在超长时自动安全截断，可以采用字节级切片：



Python

```
def truncate_to_bytes(text: str, max_bytes: int = 4096) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    # 截断字节并忽略末尾可能被切碎的半个字符
    return encoded[:max_bytes].decode("utf-8", errors="ignore")
```

**`max_length` 调大对性能与资源的影响**



在 Milvus 中，将 `max_length` 调大（如调至 16384 或 65535）**对存储和检索性能几乎没有负面影响**。



- **存储空间（按实际占用计算）**：Milvus 底层采用 Apache Arrow/Parquet 格式存储标量数据，VARCHAR 是**变长存储**。写入 200 字节的内容就只占 200 字节加微量 offset 开销，不会因为 `max_length=65535` 而预先填满空白填充位。
- **向量检索性能（不受影响）**：ANN 向量检索（Dense/Sparse Vector）只在向量索引文件中进行，VARCHAR 字段只在最终返回属性（Output Fields）时按需加载。
- **内存占用（影响极轻微）**：加载 Collection 或进行标量过滤时，Milvus 会根据实际数据量构建缓存，不会为每行直接分配 64KB 物理内存。
- **BM25 / 全文索引耗时（取决于实际长度）**：分词与倒排索引构建的耗时仅取决于文本的**实际 Token 数量**，与定义的 `max_length` 上限数值无关。

**推荐配置方案**



| **分块大小（Chunk Size）** | **建议 max_length** | **纯中文安全容量** | **说明**                             |
| -------------------------- | ------------------- | ------------------ | ------------------------------------ |
| 500 ~ 1,000 Tokens         | **8,192** (8 KB)    | 约 2,700 字        | 足够容纳常见段落及微量 Markdown 标记 |
| 1,000 ~ 2,000 Tokens       | **16,384** (16 KB)  | 约 5,400 字        | 适合包含长代码块、大表格的复杂切块   |
| 极端大 Chunk / 整篇短文    | **65,535** (64 KB)  | 约 21,800 字       | Milvus VARCHAR 的硬性上限            |

直接将 `chunk_text` 的 `max_length` 提高至 **8192** 或 **16384**，文本切分交给 Splitter 处理即可，兼顾安全与系统稳定性。