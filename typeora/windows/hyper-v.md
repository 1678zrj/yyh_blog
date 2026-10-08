# [Windows10中关于开启和关闭Hyper-V的命令](https://www.cnblogs.com/Attempts-blog/p/14720757.html)

1.  怎么操作

使用win+x,然后点击a，以管理员身份运行cmd

关闭命令：

> ```shell
> bcdedit set hypervisorlaunchtype off
> ```

开启命令：

> ```shell
> bcdedit set hypervisorlaunchtype auto
> ```

2.  为什么这么做？
    

Hyper-V是windows中自带的虚拟化技术，如果Windows中安装了Docker，想要正常使用的话就必须开启Hyper-V，否则会出现这样的错误提示。

![image-20210430090400503](https://i.loli.net/2021/04/30/nWxQzaPNRvImCEf.png)

但是如果Windows系统中同时存在VMware虚拟机的话，开启Hyper-V又会导致虚拟机用不了，这个时候就只能关闭Hyper-V。

![img](https://i.loli.net/2021/04/30/ZTKrI7Xn3f1Gxvo.png)

所以目前的解决方案只能是用虚拟机时关闭Hyper-V，用docker的时候再关闭。或者选择卸载Windows的Docker，把它安装到虚拟机系统里，毕竟人家Docker原生的应用系统就是Linux......



## 启用或关闭Windows功能中Hyper-V和虚拟机平台的功能是什么？它们会影响docker和vmware吗？它们又怎么影响virtual box的？

### deepseek:

在Windows操作系统中，启用或禁用**Hyper-V**和**虚拟机平台**功能会直接影响虚拟化工具的运行方式和兼容性。以下是具体影响和配置建议：

---

### **1. Hyper-V 的功能与影响**
#### **功能说明**
- **Hyper-V**：微软的**硬件虚拟化技术**，允许在物理主机上创建和管理虚拟机（VM）。
- **虚拟机平台**（Windows Hypervisor Platform）：提供底层的虚拟化框架，支持第三方虚拟化工具（如Docker Desktop、Android模拟器）通过标准接口调用Hyper-V。

#### **启用后的影响**
- **Docker**：
  - **Docker Desktop（Windows）**默认依赖Hyper-V运行**Linux容器**（需启用"使用Windows容器"选项时可能不依赖）。
  - 若启用Hyper-V，Docker会使用基于Hyper-V的**Linux虚拟机**（旧版）或**WSL 2**（推荐）。
  - **关闭Hyper-V**会导致Docker无法运行Linux容器（需切换回较慢的Hyper-V虚拟机或Windows容器模式）。

- **VMware Workstation/Player**：
  - 传统VMware依赖自己的**独立Hypervisor**（如`vmware-vmx.exe`），与Hyper-V冲突。
  - **VMware 15.5+**支持在启用Hyper-V时运行，但会强制使用**Windows Hypervisor Platform (WHP)**，性能可能下降。
  - 若需使用VMware原生性能，需**完全关闭Hyper-V和虚拟机平台**。

- **VirtualBox**：
  - VirtualBox 6.0+ 支持**Hyper-V兼容模式**（需启用“启用嵌套虚拟化”）。
  - 启用Hyper-V后，VirtualBox会通过WHP运行虚拟机，但**性能显著降低**（尤其是I/O和图形渲染）。
  - **VirtualBox 6.1以下版本**无法与Hyper-V共存，会直接报错。

---

### **2. 虚拟机平台（Windows Hypervisor Platform）的功能与影响**
#### **功能说明**
- 提供标准化的虚拟化接口，允许第三方工具（如WSL 2、Android模拟器）调用Hyper-V底层功能。
- 若禁用此功能，**WSL 2无法运行**，Docker Desktop也会退回较慢的Hyper-V虚拟机模式。

#### **启用后的影响**
- **必须与Hyper-V同时启用**：WSL 2和依赖虚拟机平台的工具需要两者均启用。
- **对VMware/VirtualBox的影响**：与Hyper-V类似，可能强制第三方工具通过WHP运行虚拟机。

---

### **3. 如何管理这些功能**
#### **启用/禁用方法**
1. **通过控制面板**：
   
   - 打开“控制面板” > “程序” > “启用或关闭Windows功能”。
   - 勾选或取消勾选 **Hyper-V** 和 **虚拟机平台**。
   - **重启生效**。
   
2. **通过PowerShell**：
   ```powershell
   # 启用Hyper-V和虚拟机平台
   Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V, Microsoft-Hyper-V-Management-PowerShell, VirtualMachinePlatform
   
   # 禁用Hyper-V和虚拟机平台
   Disable-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V, Microsoft-Hyper-V-Management-PowerShell, VirtualMachinePlatform
   ```
   - 重启后生效。

---

### **4. 配置建议**
#### **场景1：优先使用Docker（WSL 2）和Hyper-V虚拟机**
- **启用**：Hyper-V + 虚拟机平台。
- **结果**：
  - Docker性能最佳（通过WSL 2）。
  - VMware/VirtualBox性能下降，但可用（需新版本）。
  - 完全关闭其他虚拟化工具的原生Hypervisor。

#### **场景2：优先使用VMware或VirtualBox原生性能**
- **禁用**：Hyper-V + 虚拟机平台。
- **结果**：
  - VMware/VirtualBox以原生性能运行。
  - Docker无法使用WSL 2和Hyper-V，需切换回较慢的Linux虚拟机或Windows容器。

#### **场景3：混合使用（需Windows 10/11 最新版）**
- **启用虚拟机平台，禁用Hyper-V**：
  - 允许部分工具（如WSL 2）通过WHP运行。
  - VMware 15.5+和VirtualBox 6.0+可调用WHP，但性能弱于原生模式。

---

### **5. 常见问题解决**
- **错误“虚拟机监控程序未运行”**：
  - 确保BIOS中启用虚拟化（Intel VT-x/AMD-V）。
  - 关闭其他虚拟化工具冲突（如某些杀毒软件的虚拟化功能）。

- **Docker无法启动**：
  - 若禁用Hyper-V，需在Docker设置中切换为**Windows容器**或旧版Linux虚拟机模式。

- **VMware/VirtualBox虚拟机卡顿**：
  - 升级到最新版本（VMware 17+/VirtualBox 7.0+）。
  - 在VMware设置中选择“使用Windows Hypervisor Platform”。

---

### **总结**
| 功能           | 启用影响                                    | 禁用影响                              |
| -------------- | ------------------------------------------- | ------------------------------------- |
| **Hyper-V**    | Docker性能优化，但VMware/VirtualBox性能下降 | VMware/VirtualBox原生性能，Docker受限 |
| **虚拟机平台** | WSL 2和第三方虚拟化工具可用，但依赖Hyper-V  | WSL 2不可用，部分工具无法运行         |

根据需求灵活开关功能，重启后生效。

### ChatGpt:

### **Hyper-V 和虚拟机平台功能介绍**

在 Windows 的 **“启用或关闭 Windows 功能”** 选项中，有两个和虚拟化相关的主要功能：

1. **Hyper-V**：微软自带的 Type-1 虚拟机管理程序（Hypervisor）。
2. **虚拟机平台（Virtual Machine Platform）**：用于支持 WSL 2（Windows Subsystem for Linux 2）和 Windows 上的部分虚拟化功能。

------

### **对 Docker 的影响**

- **Hyper-V 启用后**：Docker Desktop 可使用 Hyper-V 运行基于 Linux 的容器（Windows 容器本身也依赖 Hyper-V）。
- **Hyper-V 关闭但启用“虚拟机平台”**：Docker 仍可运行，但主要通过 WSL 2 作为后端。
- **Hyper-V 和“虚拟机平台”都关闭**：Docker 只能使用 Windows 本机模式，不能运行 Linux 容器。

------

### **对 VMware 的影响**

- 启用了 Hyper-V

  ：

  - 过去 VMware Workstation 无法运行（因为 Hyper-V 占用了 VT-x 硬件加速）。
  - 现在（VMware 15.5.5+）支持与 Hyper-V 共存，但性能较差（VMware 运行在 Hyper-V 之上）。

- Hyper-V 关闭

  ：

  - VMware 可以完全利用 VT-x，性能最佳。

------

### **对 VirtualBox 的影响**

- Hyper-V 启用后：
  - 低版本（6.0 之前）：VirtualBox 不能运行 64 位 VM。
  - 高版本（6.1+）：可以运行，但性能下降（VirtualBox 运行在 Hyper-V 之上）。
- Hyper-V 关闭：
  - VirtualBox 可完全利用 VT-x，性能最佳。

------

### **总结**

| 功能                   | Docker          | VMware         | VirtualBox     |
| ---------------------- | --------------- | -------------- | -------------- |
| **Hyper-V 开启**       | 正常运行        | 支持但性能下降 | 运行但性能下降 |
| **仅开启“虚拟机平台”** | 通过 WSL2 运行  | 可能不兼容     | 可能不兼容     |
| **两者都关闭**         | 仅 Windows 容器 | 最高性能       | 最高性能       |

如果你要运行 **VMware 或 VirtualBox**，建议关闭 Hyper-V。
 如果你要运行 **Docker（Linux 容器）**，可以使用 WSL2 作为后端，而不启用完整的 Hyper-V。