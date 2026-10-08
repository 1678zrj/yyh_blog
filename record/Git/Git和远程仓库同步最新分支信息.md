```sh
(study) PS D:\mycode\python\file_receive> git branch -a
  feature/redis
* main
  remotes/origin/feature/redis
  remotes/origin/main
```

### 问：我进行PR之后将远程仓库的feature/redis分支删了，但是在本地仓库执行命令输出如上所示

这非常正常。Git 不会自动帮你清理已经从远程删除的分支，因为它默认会认为你在本地可能还有未合并的代码，或者你想留着这份历史记录以供参考，所以它不会擅自帮你删除这些“远程跟踪分支”。

要清理这些陈旧的引用，需要手动操作两步：

### 第一步：清理远程跟踪分支 (Remote Tracking Branch)

执行 `git fetch --prune`，这会告诉 Git：“去拉取最新的远程信息，并将那些远程已经不存在的分支在本地的引用删掉。”

Bash

```
git fetch --prune
```

执行后，你再输入 `git branch -a`，应该就看不到 `remotes/origin/feature/redis` 了。

### 第二步：清理本地分支 (Local Branch)

从你之前的 `git branch -a` 输出可以看到，你本地还有一个 `feature/redis` 分支（就是第一行那个没有前缀的）：

Plaintext

```
  feature/redis
* main
```

既然 PR 已经合并，这个本地分支通常也就没用了，建议将其删除以保持整洁：

1. **先确保你切换回了 `main` 分支**：

   Bash

   ```
   git checkout main
   ```

2. **删除本地分支**：

   Bash

   ```
   git branch -d feature/redis
   ```

   *(注意：如果 Git 提示你该分支未完全合并，且你确定 PR 已经合了，可以使用 `-D` 强制删除：`git branch -D feature/redis`)*

执行完这两步，你的仓库状态就彻底“清爽”了！