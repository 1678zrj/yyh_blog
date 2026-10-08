## 问：Vue如何使用Element-plus

在 Vue 项目中使用 [Element Plus](https://element-plus.org/zh-CN/) 的基本步骤如下：

------

## ✅ 一、安装 Element Plus

你需要先安装 `element-plus`，确保你的项目使用的是 Vue 3（Element Plus 是为 Vue 3 设计的）。

在项目根目录下运行：

```bash
npm install element-plus
# 或者使用 yarn
# yarn add element-plus
```

------

## ✅ 二、全局引入 Element Plus（推荐）

在 `main.js` 或 `main.ts` 中引入 Element Plus 和其样式：

```js
// main.js 或 main.ts
import { createApp } from 'vue'
import App from './App.vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'

const app = createApp(App)

app.use(ElementPlus)
app.mount('#app')
```

------

## ✅ 三、在组件中使用 Element Plus 组件

以 `el-button` 为例：

```vue
<template>
  <el-button type="primary" @click="handleClick">主要按钮</el-button>
</template>

<script setup>
function handleClick() {
  console.log('按钮点击了')
}
</script>
```

------

## ✅ 四、按需引入（可选，减小体积）

使用官方提供的自动引入插件 `unplugin-vue-components` 和 `unplugin-auto-import`。

### 安装依赖：

```bash
npm install -D unplugin-vue-components unplugin-auto-import
```

### 修改 `vite.config.js`（如果你用的是 Vite）：

```js
import Components from 'unplugin-vue-components/vite'
import AutoImport from 'unplugin-auto-import/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'

export default {
  plugins: [
    AutoImport({
      resolvers: [ElementPlusResolver()],
    }),
    Components({
      resolvers: [ElementPlusResolver()],
    }),
  ],
}
```

这样就可以在组件中直接写 `<el-button>` 而无需手动 `import`。

------

## ✅ 五、常见问题排查

- ❌ 没引入样式？→ `import 'element-plus/dist/index.css'`。
- ❌ 组件名写错了？→ 是 `el-button`，不是 `ElButton`。
- ❌ Vue 版本不兼容？→ Element Plus 只支持 **Vue 3**。
- ❌ 样式失效？→ 可能是样式被其他 CSS 框架覆盖了，检查是否被 reset。

------

需要我为你做一个包含 Element Plus 的 Vue 示例项目吗？或者你想了解某个组件怎么用？

## 问：为什么要import ElementPlus from 'element-plus' import 'element-plus/dist/index.css'两次import？

这是一个非常常见但容易混淆的问题，下面详细解释：

------

## 🔍 为什么需要这两个 import？

```js
import ElementPlus from 'element-plus'              // 👉 导入组件库功能（JavaScript）
import 'element-plus/dist/index.css'                // 👉 导入组件库的样式（CSS）
```

------

### ✅ 1. `import ElementPlus from 'element-plus'`

这条语句引入的是 **Element Plus 的 JavaScript 模块**，它包含：

- 所有的 Vue 组件定义（如 `ElButton`, `ElTable` 等）
- 插件注册逻辑（`app.use(ElementPlus)`）
- 一些工具函数（如消息提示 `ElMessage`）

💡 **这是功能逻辑的核心**，没这个就无法使用组件。

------

### ✅ 2. `import 'element-plus/dist/index.css'`

这条语句引入的是 **Element Plus 的 CSS 样式文件**，它包含：

- 所有组件的基础样式，比如按钮的背景颜色、表格的边框样式等。
- 没有它，组件会渲染，但 **样式会错乱或看起来像原始 HTML 元素**。

💡 这是组件的“皮肤”，没有这个就像房子没装饰。

------

## 🧠 类比理解

就像你安装了一台咖啡机：

- `import ElementPlus` 是安装好了咖啡机（功能）
- `import 'element-plus/dist/index.css'` 是给咖啡机配上漂亮的外壳（样式）

两者缺一不可。

------

## ✅ 总结

| 引入内容                               | 作用                    | 是否必须 |
| -------------------------------------- | ----------------------- | -------- |
| `import ElementPlus`                   | 注册组件功能和 JS 模块  | ✅ 是     |
| `import 'element-plus/dist/index.css'` | 提供组件的默认 CSS 样式 | ✅ 是     |

------

如果你采用了 **按需引入 + 样式自动导入插件**，样式引入可以由插件自动完成，就可以省略 `index.css`。但如果你是手动全局引入的，两个都必须写。

需要我给你举个样式不引入时按钮变丑的示例对比吗？

## 问：其它的CSS框架也是这样引入吗

是的，**大多数 CSS 框架或 UI 组件库在 Vue 或其他前端项目中都需要分开引入 “功能逻辑（JS）” 和 “样式（CSS）”**。这是非常常见的做法。下面我用几个常见的 CSS/UI 框架来举例说明：

------

## ✅ 1. **Element Plus（Vue 3 专用）**

```js
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
```

- `element-plus`: 引入 JS 组件功能。
- `index.css`: 引入所有组件的默认样式。

------

## ✅ 2. **Ant Design Vue**

```js
import Antd from 'ant-design-vue'
import 'ant-design-vue/dist/antd.css'
```

- 同样是 JS 和 CSS 分开引入。
- 如果只引入 JS 组件而不引入 CSS，样式会错乱或失效。

------

## ✅ 3. **Vuetify（Vue 3）**

Vuetify 是比较特殊的，它建议一次性通过 `createVuetify()` 配置，同时也要单独引入 CSS：

```js
import { createVuetify } from 'vuetify'
import 'vuetify/styles'                 // 样式
import { aliases, mdi } from 'vuetify/iconsets/mdi'

const vuetify = createVuetify({
  icons: { defaultSet: 'mdi', aliases, sets: { mdi } },
})
```

------

## ✅ 4. **Bootstrap（全平台）**

```js
import 'bootstrap/dist/css/bootstrap.min.css'
import 'bootstrap/dist/js/bootstrap.bundle.min.js'
```

- 它不是 Vue 组件库，但同样样式和脚本分开引入。

------

## ✅ 5. **Tailwind CSS**

Tailwind 是一个纯样式框架，没有 JS 功能组件，所以只引入 CSS：

```js
// 在 main.js 或 main.css 中
import './index.css' // 包含 @tailwind 指令
```

------

## ✅ 总结

| 框架名         | 是否分开引入 JS 和 CSS | 特别说明                       |
| -------------- | ---------------------- | ------------------------------ |
| Element Plus   | ✅ 是                   | Vue 3 推荐组件库               |
| Ant Design Vue | ✅ 是                   | Vue 2/3 通用，样式必须手动引入 |
| Vuetify        | ✅ 是                   | 使用 createVuetify 配置样式    |
| Bootstrap      | ✅ 是                   | 经典 CSS + JS 分离结构         |
| Tailwind CSS   | ❌ 只有 CSS             | 没有组件 JS，全是样式类        |

------

如果你想要更简洁的引入方式，比如不用手写样式引入，可以使用按需引入+自动样式导入插件，比如：

```bash
npm install unplugin-vue-components unplugin-auto-import
```

## 问：既然element-plus可以引入 JS 组件功能和引入所有组件的默认样式。那么element-plus有它专有的js函数是吗？还是别的？

是的，**`element-plus` 不仅提供 Vue 组件（如 `<el-button>`、`<el-table>`），它还提供了一些专用的 JS 函数和全局方法**，这些方法可以脱离模板，在 JavaScript/TypeScript 代码中直接使用。

------

## ✅ 一、Element Plus 的专有 JS 函数有哪些？

这些函数主要是用于：

- 弹出消息（提示、警告、错误等）
- 弹出确认框、消息框
- 手动调用加载组件（如全屏 Loading）
- 通知（Notification）

------

### 🌟 常用 JS 函数一览

| 功能             | 用法示例                                               | 描述                    |
| ---------------- | ------------------------------------------------------ | ----------------------- |
| `ElMessage`      | `ElMessage({ message: '成功', type: 'success' })`      | 顶部轻提示              |
| `ElMessageBox`   | `ElMessageBox.confirm(...)`                            | 弹出确认/警告框         |
| `ElLoading`      | `ElLoading.service({ fullscreen: true })`              | 加载中遮罩（全屏/局部） |
| `ElNotification` | `ElNotification({ title: '提示', message: '已完成' })` | 右上角通知弹窗          |

------

## ✅ 二、如何使用这些函数？

你必须在 `main.js` 中先引入 Element Plus，然后在组件里使用这些函数（直接使用即可，不需要 `import`，因为 `app.use(ElementPlus)` 后全局挂载了）。

### 示例：ElMessage

```vue
<template>
  <el-button type="success" @click="notify">点我提示</el-button>
</template>

<script setup>
function notify() {
  ElMessage({
    message: '操作成功！',
    type: 'success',
  })
}
</script>
```

------

### 示例：ElMessageBox

```vue
<template>
  <el-button type="warning" @click="showConfirm">删除</el-button>
</template>

<script setup>
function showConfirm() {
  ElMessageBox.confirm('你确定要删除这条记录吗？', '警告', {
    confirmButtonText: '确定',
    cancelButtonText: '取消',
    type: 'warning',
  }).then(() => {
    ElMessage.success('删除成功！')
  }).catch(() => {
    ElMessage.info('已取消')
  })
}
</script>
```

------

## ✅ 三、如果你用的是 TypeScript

你可以单独 `import`：

```ts
import { ElMessage, ElMessageBox, ElNotification, ElLoading } from 'element-plus'

ElMessage.success('你好，TypeScript！')
```

------

## ✅ 总结

- ✅ `element-plus` 不止有组件，还有专属 JS 函数（`ElMessage`, `ElMessageBox`, `ElLoading`, `ElNotification` 等）
- ✅ 这些函数用于全局控制（弹窗、加载、提示等），不依赖模板组件
- ✅ 默认全局注册，如果你手动按需引入，也可以自己 `import`



###### 