## 问：如何查看主流大语言模型的jinja模板

查看主流大语言模型使用的 **Jinja 模板** 是理解其提示词组织方式的关键途径，尤其在使用开源框架（如 OpenChat、LLaMA, DeepSeek, OpenFunction, LangChain）进行 Function Calling、Tool Use 或 Agent 构建时。下面是获取和查看这些模板的几种主流方式：

------

## ✅ 方法一：从 GitHub 项目源码中查看

### 🔹1. **OpenChat / LLaMA.cpp / FastChat / DeepSeek 等开源模型**

它们的模板往往在以下路径：

#### 🔸OpenChat（基于 FastChat）

GitHub: https://github.com/imoneoi/openchat
 模板文件路径：

```
fastchat/conversation.py
```

Jinja 模板示例（简化）：

```python
prompt_template = """<|system|>{{ system_message }}<|user|>{{ user_message }}<|assistant|>"""
```

------

#### 🔸FastChat（支持 ChatML, LLaMA, OpenChat 等）

GitHub: https://github.com/lm-sys/FastChat
 模板定义位置：

```
fastchat/conversation.py
fastchat/conversation_template/*
```

------

#### 🔸DeepSeek-VL / DeepSeek-Coder

GitHub: https://github.com/deepseek-ai
 搜索：

```
prompt_templates/*
```

------

## ✅ 方法二：查看 HuggingFace 模型页面说明

例如在 HuggingFace 上的主流模型（如 Mistral, LLaMA3, Zephyr）通常会在 README 或 `tokenizer_config.json` 中写明模板。

🔹例如：

```json
{
  "chat_template": "{% for message in messages %}...{% endfor %}"
}
```

你可以直接查看模型页的 `Files and versions` → 搜索 `tokenizer_config.json` → 查 `chat_template` 字段。

示例：

- https://huggingface.co/mistralai/Mistral-7B-Instruct-v0.2/blob/main/tokenizer_config.json

------

## ✅ 方法三：使用 `transformers` 工具加载模板

如果你使用的是 `transformers` 库：

```python
from transformers import AutoTokenizer

tokenizer = AutoTokenizer.from_pretrained("mistralai/Mistral-7B-Instruct-v0.2")
print(tokenizer.chat_template)
```

------

## ✅ 方法四：LangChain / LangGraph 的 PromptTemplate 使用

LangChain 提供标准类：

```python
from langchain.prompts import ChatPromptTemplate
```

你可以通过 `ChatPromptTemplate.from_messages()` 来查看结构化模板，也可以在链中直接定义 Jinja 模板：

```python
prompt = PromptTemplate(
    input_variables=["tool_calls", "user_input"],
    template="You are a tool caller. {{ tool_calls }} based on user input: {{ user_input }}"
)
```

------

## ✅ 方法五：使用 LLM 工具链（如 OpenFunction）

如果你在使用 [`OpenFunction`](https://github.com/openfunctions/openfunctions)（专注于 Function Calling 的开源框架），Jinja 模板一般位于：

```
openfunctions/templates/*
```

------

## 🔚 总结：如何查看主流 LLM 的 Jinja 模板

| 方式                       | 说明                                             |
| -------------------------- | ------------------------------------------------ |
| ✅ GitHub 源码              | 最直接，适合 OpenChat、FastChat、DeepSeek、LLaMA |
| ✅ HF tokenizer_config      | HuggingFace 的模型通常包含 `chat_template`       |
| ✅ `transformers` 库        | 自动加载并打印模板                               |
| ✅ LangChain PromptTemplate | 自定义 Agent 流程模板                            |
| ✅ OpenFunction / LangGraph | 查看 Agent 编排工具自带的模板系统                |

------

📌 如果你告诉我你具体在用哪个模型或框架（如 Mistral, LLaMA3, DeepSeek, FastChat, LangGraph），我可以帮你定位它的模板位置或直接展示给你看。是否需要？