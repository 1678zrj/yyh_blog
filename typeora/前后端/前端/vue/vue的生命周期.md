# vue的生命周期

在 Vue.js 中，**生命周期钩子函数**是开发者可以利用的关键点，方便我们在组件创建、更新和销毁的各个阶段执行特定逻辑。Vue 的生命周期可以分为以下三个主要阶段：

1. **创建阶段**
2. **挂载阶段**
3. **更新阶段**
4. **销毁阶段**

以下是详细的讲解及示例：

------

### 1. **创建阶段**

组件实例被创建，但尚未挂载到 DOM 中。

#### (1) **`beforeCreate`**

- **触发时机**：实例刚刚初始化，但组件的 data、methods、computed 和 watch 等配置尚未初始化。

- 特点

  ：

  - 你无法访问 `data` 和 `methods` 中的内容。
  - 适用于在初始化之前执行一些逻辑，比如全局事件监听或加载环境变量。

```javascript
beforeCreate() {
  console.log('beforeCreate: 组件实例已初始化');
}
```

#### (2) **`created`**

- **触发时机**：实例已创建，`data`、`methods`、`computed` 和 `watch` 都已初始化，但尚未挂载到 DOM。

- 特点

  ：

  - 可以访问并操作 `data` 和 `methods`。
  - 适用于数据的初始化、请求数据等操作。

```javascript
created() {
  console.log('created: 组件已创建，data 可用');
  console.log('当前数据:', this.message); // 假设 data 中有 message
}
```

------

### 2. **挂载阶段**

组件被挂载到 DOM 中。

#### (3) **`beforeMount`**

- **触发时机**：模板已编译完成，组件即将挂载到 DOM，但真实的 DOM 还没有生成。

- 特点

  ：

  - 此时虚拟 DOM 已经生成，但尚未实际渲染到页面。
  - 可以在此阶段查看即将挂载的 DOM 树。

```javascript
beforeMount() {
  console.log('beforeMount: 组件即将挂载');
}
```

#### (4) **`mounted`**

- **触发时机**：组件挂载到真实 DOM 中后触发。

- 特点

  ：

  - DOM 已经可用，可以进行 DOM 操作。
  - 适用于执行依赖 DOM 的操作，如获取节点信息或初始化第三方库。

```javascript
mounted() {
  console.log('mounted: 组件已挂载到 DOM');
  console.log(this.$refs.myElement); // 假设有一个 ref="myElement"
}
```

------

### 3. **更新阶段**

组件的响应式数据发生变化，触发 DOM 更新。

#### (5) **`beforeUpdate`**

- **触发时机**：组件数据更新，DOM 即将重新渲染。

- 特点

  ：

  - 数据已更新，但 DOM 还未重新渲染。
  - 适用于在更新前对数据进行校验或修改。

```javascript
beforeUpdate() {
  console.log('beforeUpdate: 数据已更新，DOM 即将更新');
}
```

#### (6) **`updated`**

- **触发时机**：组件数据更新完成，DOM 也已完成重新渲染。

- 特点

  ：

  - 数据和 DOM 都已同步更新。
  - 适用于操作最新的 DOM，但要避免二次更新陷入死循环。

```javascript
updated() {
  console.log('updated: 数据和 DOM 已同步更新');
}
```

------

### 4. **销毁阶段**

组件从 DOM 中移除。

#### (7) **`beforeUnmount`** （Vue 3）/ **`beforeDestroy`** （Vue 2）

- **触发时机**：组件即将销毁，但实例仍然完全可用。

- 特点

  ：

  - 适用于清理定时器、取消事件监听或断开网络请求。

```javascript
beforeUnmount() { // Vue 3
  console.log('beforeUnmount: 组件即将销毁');
}
// 或
beforeDestroy() { // Vue 2
  console.log('beforeDestroy: 组件即将销毁');
}
```

#### (8) **`unmounted`** （Vue 3）/ **`destroyed`** （Vue 2）

- **触发时机**：组件销毁完成，所有的绑定和事件监听都被移除。

- 特点

  ：

  - 无法再访问组件的实例。
  - 最终的清理阶段。

```javascript
unmounted() { // Vue 3
  console.log('unmounted: 组件已销毁');
}
// 或
destroyed() { // Vue 2
  console.log('destroyed: 组件已销毁');
}
```

------

### Vue 生命周期示意图

```
创建阶段： beforeCreate -> created
挂载阶段： beforeMount -> mounted
更新阶段： beforeUpdate -> updated
销毁阶段： beforeUnmount/destroy -> unmounted/destroyed
```

------

### 使用示例：

```javascript
<template>
  <div>
    <h1>{{ message }}</h1>
    <button @click="updateMessage">更新消息</button>
  </div>
</template>

<script>
export default {
  data() {
    return {
      message: "Hello, Vue.js!"
    };
  },
  beforeCreate() {
    console.log("beforeCreate");
  },
  created() {
    console.log("created");
  },
  beforeMount() {
    console.log("beforeMount");
  },
  mounted() {
    console.log("mounted");
  },
  beforeUpdate() {
    console.log("beforeUpdate");
  },
  updated() {
    console.log("updated");
  },
  beforeUnmount() {
    console.log("beforeUnmount");
  },
  unmounted() {
    console.log("unmounted");
  },
  methods: {
    updateMessage() {
      this.message = "Message Updated!";
    }
  }
};
</script>
```

运行代码时，通过控制台可以观察生命周期钩子触发的顺序和时机。

------

### 注意：

1. Vue 3 将 Vue 2 的 `beforeDestroy` 和 `destroyed` 替换为 `beforeUnmount` 和 `unmounted`。
2. 在 Vue 3 中，可以结合 **Composition API**（如 `onMounted`、`onUnmounted` 等）替代部分生命周期钩子。

# DOM是指什么

**DOM** 是 **Document Object Model** 的缩写，翻译为 **文档对象模型**。它是一种编程接口，用于表示 HTML 和 XML 文档的结构，并提供操作这些文档的能力。

在浏览器中，HTML 页面被解析后，会生成 DOM 树。开发者可以通过 JavaScript 来访问和修改这棵树，从而动态更新网页内容、样式和行为。

------

### DOM 的作用

1. **表示网页结构**
   - DOM 将 HTML 和 XML 文档表示为树形结构。
   - 每个节点对应 HTML 文档中的一个元素、属性或文本。
2. **允许动态操作网页**
   - 通过 DOM，开发者可以用 JavaScript 操作页面，比如：
     - 修改内容（如改变文字或图片）。
     - 动态添加、删除 HTML 元素。
     - 改变样式（如更改颜色或隐藏元素）。
     - 处理用户交互（如点击按钮触发事件）。
3. **实现浏览器与脚本语言的桥梁**
   - DOM 是浏览器和 JavaScript 之间的桥梁，使 JavaScript 能够操作网页。

------

### DOM 树结构

HTML 文档会被解析为树状结构，称为 **DOM 树**，如下所示：

#### 示例 HTML：

```html
<!DOCTYPE html>
<html>
  <head>
    <title>DOM 示例</title>
  </head>
  <body>
    <h1>Hello, DOM!</h1>
    <p>这是一个段落。</p>
  </body>
</html>
```

#### 对应的 DOM 树：

```
- Document
  - html
    - head
      - title
        - "DOM 示例"
    - body
      - h1
        - "Hello, DOM!"
      - p
        - "这是一个段落。"
```

------

### 操作 DOM 的方法

使用 JavaScript 可以轻松操作 DOM，比如：

#### 1. **获取 DOM 元素**

- 通过 ID 获取：

  ```javascript
  const element = document.getElementById("myId");
  ```

- 通过类名获取：

  ```javascript
  const elements = document.getElementsByClassName("myClass");
  ```

- 通过标签名获取：

  ```javascript
  const elements = document.getElementsByTagName("div");
  ```

- 通过 CSS 选择器获取：

  ```javascript
  const element = document.querySelector(".myClass"); // 单个匹配
  const elements = document.querySelectorAll("div"); // 所有匹配
  ```

#### 2. **修改内容**

- 修改某个元素的文本：

  ```javascript
  const element = document.getElementById("myId");
  element.textContent = "新内容";
  ```

#### 3. **修改样式**

- 直接更改元素样式：

  ```javascript
  const element = document.getElementById("myId");
  element.style.color = "red";
  element.style.fontSize = "20px";
  ```

#### 4. **添加/删除元素**

- 添加一个新元素：

  ```javascript
  const newElement = document.createElement("p");
  newElement.textContent = "这是新段落";
  document.body.appendChild(newElement);
  ```

- 删除一个元素：

  ```javascript
  const element = document.getElementById("myId");
  element.remove();
  ```

#### 5. **事件监听**

- 添加点击事件：

  ```javascript
  const button = document.getElementById("myButton");
  button.addEventListener("click", function () {
    alert("按钮被点击了！");
  });
  ```

------

### 重点术语

1. **节点（Node）**
    DOM 中的每个元素、属性和文本都被称为节点，分为以下几类：
   - **元素节点**：HTML 标签（如 `<div>`）。
   - **文本节点**：标签内部的文本（如 `"Hello, DOM!"`）。
   - **属性节点**：元素的属性（如 `id="myId"`）。
2. **父节点和子节点**
   - 父节点：包含其他节点的节点。
   - 子节点：被包含的节点。
   - 示例：`<body>` 是 `<h1>` 和 `<p>` 的父节点。
3. **根节点**
   - 整个 DOM 树的顶层节点，通常是 `Document` 对象。

------

### 实用示例

#### HTML 示例：

```html
<!DOCTYPE html>
<html>
  <body>
    <h1 id="header">欢迎来到 DOM</h1>
    <button id="changeText">改变标题</button>
  </body>
</html>
```

#### JavaScript 操作：

```javascript
// 获取按钮和标题
const button = document.getElementById("changeText");
const header = document.getElementById("header");

// 点击按钮时改变标题内容
button.addEventListener("click", function () {
  header.textContent = "标题已更改！";
  header.style.color = "blue"; // 修改样式
});
```

------

### 总结

**DOM** 是开发者动态操作网页的核心工具。掌握 DOM 的结构和操作方法，可以让你更高效地开发交互式网页应用！

# 通俗理解DOM

好！我们用更通俗的方式来理解 **DOM**，想象它是浏览器用来“理解”和“操作”网页的一个 **树状地图**。

------

### 什么是 DOM？

1. **网页就像一本书**
   - 书里有标题、段落、图片等内容。
   - 如果想用程序操作这本书，比如加一段文字、改标题或者删掉某张图片，就需要一种“操作书的方法”。
   - **DOM 就是浏览器帮你把网页变成的一棵树**，让程序知道书的每个部分在哪里，还能改动它们。
2. **DOM 是网页的骨架**
   - 一个网页通常是 HTML 写成的，这些 HTML 代码会被浏览器解析成一棵“树”。
   - 这棵树的每个分支或叶子，代表页面上的一个部分，比如文字、按钮、图片等。

------

### 打个比方

#### 假设你有一个网页，HTML 长这样：

```html
<!DOCTYPE html>
<html>
  <body>
    <h1>欢迎来到我的网站</h1>
    <p>这里有很多内容！</p>
    <button>点击我</button>
  </body>
</html>
```

浏览器看这个网页后，就会心里画出一棵“树”：

```
网页 (Document)
└── html
    ├── body
    │   ├── h1 ("欢迎来到我的网站")
    │   ├── p ("这里有很多内容！")
    │   └── button ("点击我")
```

- **树根**：整棵树的起点（`Document`）。
- **分支**：HTML 标签，比如 `html`、`body`、`h1`。
- **叶子**：具体的内容，比如 `“欢迎来到我的网站”`。

------

### 为什么需要 DOM？

有了 DOM，你就能用 **JavaScript** 操作网页，比如：

- **改标题的内容**：把“欢迎来到我的网站”改成“你好，世界！”
- **加一个新段落**：比如“这是个有趣的网站！”
- **换按钮的颜色**：让按钮变成红色。

------

### 怎么用 JavaScript 操作 DOM？

假设我们想改动这个网页，步骤就像这样：

#### 1. **找到想改的地方**

你得先告诉 JavaScript，要操作哪部分内容，比如标题。

#### 2. **对它动手改**

找到之后，就能改内容、换样式，或者干脆删掉。

------

### 通俗示例

#### HTML：

```html
<!DOCTYPE html>
<html>
  <body>
    <h1 id="title">欢迎来到我的网站</h1>
    <button id="myButton">点我改标题</button>
  </body>
</html>
```

#### JavaScript：

```javascript
// 找到标题和按钮
const title = document.getElementById("title");
const button = document.getElementById("myButton");

// 点按钮时，把标题改成“你好，世界！”
button.addEventListener("click", function () {
  title.textContent = "你好，世界！";
  title.style.color = "blue"; // 同时把标题变蓝
});
```

------

### 更简单的类比

1. **DOM 就像乐高积木**
   - 网页是用一个个 HTML 标签搭建的，像乐高一样拼起来。
   - DOM 是乐高的说明书，你可以用 JavaScript 拿下某个积木块（比如标题），换个颜色或者加一个新积木。
2. **DOM 就像家里的家具布置**
   - 网页是家里的布局，标题是沙发，段落是茶几，按钮是灯。
   - DOM 是你拿着的家居指南，告诉你沙发在哪、茶几能不能换，灯能不能调亮。

------

### 最常见的 DOM 操作

1. **找到页面上的内容：** 比如用 `document.getElementById` 来找到某个按钮。
2. **改内容：** 用 `.textContent` 或 `.innerHTML` 改文字。
3. **改样式：** 用 `.style` 改颜色、字体、大小等。
4. **加新内容：** 用 `document.createElement` 创建新的 HTML 元素。
5. **监听事件：** 比如用户点了某个按钮，就触发某个功能。

------

### 总结

DOM 就是网页的**骨架**，它让 JavaScript 可以直接“抓住”网页上的每个部分，从而随意更改内容、样式或者功能。这样，网页就可以更**动态**和**交互**了，比如点击一个按钮就显示一段文字。