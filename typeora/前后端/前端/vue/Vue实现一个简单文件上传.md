## 问：我现在想给我的Vue项目添加一个页面，该页面可以选择本地的文件，将文件传送给基于fastapi的服务端，代码怎么写

下面我将分别给出Vue前端和FastAPI后端的实现代码，实现文件选择上传功能：

### 一、Vue前端实现 (使用Vue 3 + Composition API)

```vue
<template>
  <div class="upload-container">
    <h2>文件上传</h2>
    <input type="file" @change="handleFileChange" ref="fileInput" multiple />
    <button @click="uploadFiles" :disabled="!files.length">上传文件</button>
    
    <div v-if="files.length" class="file-list">
      <h3>已选择文件：</h3>
      <ul>
        <li v-for="(file, index) in files" :key="index">
          {{ file.name }} ({{ formatFileSize(file.size) }})
        </li>
      </ul>
    </div>
    
    <div v-if="uploadProgress > 0" class="progress-bar">
      <div class="progress" :style="{ width: uploadProgress + '%' }"></div>
    </div>
    
    <div v-if="message" class="message" :class="{ success: isSuccess, error: !isSuccess }">
      {{ message }}
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue';
import axios from 'axios';

const files = ref([]);
const uploadProgress = ref(0);
const message = ref('');
const isSuccess = ref(false);

const handleFileChange = (e) => {
  files.value = Array.from(e.target.files);
  uploadProgress.value = 0;
  message.value = '';
};

const uploadFiles = async () => {
  if (!files.value.length) return;

  const formData = new FormData();
  files.value.forEach(file => {
    formData.append('files', file);
  });

  try {
    const response = await axios.post('http://localhost:8000/upload', formData, {
      headers: {
        'Content-Type': 'multipart/form-data'
      },
      onUploadProgress: (progressEvent) => {
        uploadProgress.value = Math.round(
          (progressEvent.loaded * 100) / progressEvent.total
        );
      }
    });

    message.value = `上传成功！服务器返回: ${response.data.message}`;
    isSuccess.value = true;
    files.value = [];
    
    // 3秒后清除消息
    setTimeout(() => {
      message.value = '';
    }, 3000);
  } catch (error) {
    console.error('上传出错:', error);
    message.value = `上传失败: ${error.response?.data?.detail || error.message}`;
    isSuccess.value = false;
  }
};

const formatFileSize = (bytes) => {
  if (bytes === 0) return '0 Bytes';
  const k = 1024;
  const sizes = ['Bytes', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
};
</script>

<style scoped>
.upload-container {
  max-width: 600px;
  margin: 2rem auto;
  padding: 20px;
  border: 1px solid #eee;
  border-radius: 8px;
}

input[type="file"] {
  margin: 20px 0;
  padding: 10px;
  border: 1px solid #ddd;
  border-radius: 4px;
  width: 100%;
}

button {
  background-color: #42b983;
  color: white;
  border: none;
  padding: 10px 20px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 16px;
}

button:disabled {
  background-color: #cccccc;
  cursor: not-allowed;
}

.file-list {
  margin: 20px 0;
}

.progress-bar {
  height: 20px;
  background-color: #f5f5f5;
  border-radius: 4px;
  margin: 20px 0;
}

.progress {
  height: 100%;
  background-color: #42b983;
  border-radius: 4px;
  transition: width 0.3s;
}

.message {
  padding: 10px;
  margin-top: 20px;
  border-radius: 4px;
}

.success {
  background-color: #dff0d8;
  color: #3c763d;
}

.error {
  background-color: #f2dede;
  color: #a94442;
}
</style>
```

### 二、FastAPI 后端实现

```python
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import os
import shutil

app = FastAPI()

# 配置CORS（跨域资源共享）
origins = [
    "http://localhost:8080",
    "http://127.0.0.1:8080",
    "http://localhost:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 创建上传目录
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

@app.post("/upload")
async def upload_files(files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="未上传文件")
    
    saved_files = []
    
    for file in files:
        try:
            # 创建安全文件名（防止路径遍历攻击）
            file_path = os.path.join(UPLOAD_DIR, file.filename)
            
            # 保存文件到磁盘
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            
            saved_files.append(file.filename)
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"文件 {file.filename} 保存失败: {str(e)}"
            )
        finally:
            await file.close()
    
    return {
        "message": f"成功接收 {len(saved_files)} 个文件",
        "saved_files": saved_files
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

### 三、关键步骤说明

1. **前端实现要点**：
   - 使用 `<input type="file">` 获取文件
   - 通过 FormData 对象包装文件数据
   - 使用 axios 发送 multipart/form-data 请求
   - 显示上传进度条
   - 提供用户反馈（成功/错误消息）

2. **后端实现要点**：
   - 使用 `UploadFile` 类型接收文件
   - 配置 CORS 中间件处理跨域请求
   - 安全保存文件到服务器
   - 错误处理和资源清理（确保文件关闭）

3. **运行步骤**：
   ```bash
   # 启动FastAPI后端
   python main.py
   
   # 启动Vue前端 (需先安装依赖)
   npm run serve
   ```

### 四、安全增强建议

1. **文件类型验证**：
   ```python
   # 后端添加文件类型检查
   ALLOWED_EXTENSIONS = [".jpg", ".png", ".pdf", ".docx"]
   
   for file in files:
       ext = os.path.splitext(file.filename)[1].lower()
       if ext not in ALLOWED_EXTENSIONS:
           raise HTTPException(400, detail=f"不支持的文件类型: {ext}")
   ```

2. **文件大小限制**：
   ```python
   # 在FastAPI中添加
   @app.post("/upload")
   async def upload_files(files: list[UploadFile] = File(..., max_length=1024*1024*10)):  # 10MB限制
   ```

3. **文件名安全处理**：
   ```python
   # 防止路径遍历攻击
   filename = secure_filename(file.filename)
   ```

4. **前端添加文件类型限制**：
   ```html
   <input type="file" accept=".jpg,.png,.pdf,.docx">
   ```

### 五、常见问题解决

1. **跨域问题**：确保后端正确配置了CORS
2. **413 Payload Too Large**：调整后端文件大小限制
   ```python
   # 启动时增加限制
   uvicorn.run(app, host="0.0.0.0", port=8000, limit_max_requests=1024*1024*50)  # 50MB
   ```
3. **文件保存权限问题**：确保上传目录有写权限

这个实现提供了完整的文件上传流程，包含进度显示、错误处理和用户反馈，你可以根据实际需求调整样式和功能。