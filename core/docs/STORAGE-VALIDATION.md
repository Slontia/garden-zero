# 独立镜像验证操作

当前仅有隔离实验脚本；尚无生产 start/stop。执行前读取 NAS-PLAN.md 与 ENVIRONMENT.md。不要将已有数据交给此实验，也不要搬动 Docker Desktop 虚拟磁盘。

## 当前可运行

在 /mnt/d/nas：

```bash
bash core/tests/verify-storage.sh --offline
```

每次新建一个 256 MiB 测试镜像及日志，保留全部产物。只验证 ext4 离线读写和检查，不满足部署验收。

在具备 root、CAP_SYS_ADMIN、loop 设备的正常目标 WSL/Linux 终端，可执行：

```bash
cd /mnt/d/nas
sudo bash core/tests/verify-storage.sh --mount
```

此命令不接受已有镜像路径，也不使用物理磁盘；创建全新测试镜像，验证挂载、非 root 写入、UID/GID/0600 权限、正常卸载、loop 分离与重新附加、文件校验和权限保留。挂载点位于 /tmp，镜像位于随盘 system/storage。出错仅尝试普通卸载，不强制或 lazy 卸载；若卸载失败，必须处理挂载残留后才能弹出磁盘。它不证明生产模块已安全停止，也未验证跨主机迁移或稀疏分配。

## 下一步顺序

1. 在有正常 Windows 互操作权限的终端读取卷身份、磁盘型号、文件系统，核对 C:\nas-storage 的目标；只读列出 Docker Desktop 的 context、容器、卷、挂载映射、虚拟磁盘位置。禁止输出 .env 或 docker inspect 的完整环境。
2. 记录 Windows 构建、安全更新状态、WSL、Docker 及 Compose 版本；联网查官方兼容要求后才安装 WSL Engine。先确定旧数据位置与回退方案，不同时启动两套引擎处理同一应用数据。
3. 运行上述隔离挂载实验并归档日志；如 raw 不按需分配，比较 NTFS 显式 sparse 与动态 VHDX。创建动态 VHDX 也仅使用全新测试文件；Windows 附加和原生 Linux qemu-nbd 链路须分别实测，暂不选定工具和生产格式。
4. 新格式完成两端权限、空间占用、正常卸载与重挂后，才用固定兼容版本 PostgreSQL 做测试库写入、正常停止、重挂及行级数据校验。禁止在实验里挂入旧数据库。
5. 原生 Linux 与 WSL 对同一测试镜像顺序接续；核对容器版本/摘要、架构、UID/GID、数据校验、内部容量与外部占用。严禁双写。
6. 存储门槛通过后，实现校验稳定卷身份和 ext4 UUID 的生产启动保护、正常停止与卸载；再推进 Meta 和业务部署。所有未通过项留在计划中，不将离线检查当成验收。

本会话的执行沙箱不能提权、开网络 socket 或调用 Windows 程序；须在具备这些能力的正常宿主终端继续真实挂载验证。这是运行权限限制，不是删除或格式化旧数据的授权请求。

## 已完成与下一项

probe-zeorVOdn 的 --mount 已由用户在正常 WSL 终端运行通过，并读回日志核实。无需为同一结论重复创建镜像。

下一项在 Windows PowerShell 执行以下只读命令（不设置 sparse 标志、不修改文件）：

```powershell
Get-Volume -DriveLetter D | Select-Object DriveLetter, FileSystem, FileSystemLabel, UniqueId, Size, SizeRemaining
fsutil sparse queryflag "D:\nas\system\storage\probe-zeorVOdn\probe.img"
fsutil sparse queryrange "D:\nas\system\storage\probe-zeorVOdn\probe.img"
```

如果出现权限拒绝，在管理员 PowerShell 重试。保留原始输出；queryflag 用于查询 sparse 属性，queryrange 用于查询分配范围，需一起判断。即使设置了 sparse 属性，也不能单凭该属性断言当前已节省空间。此处不执行 setflag 或 setrange；后续稀疏实验只在新建测试文件上进行。

Windows 已确认原 probe.img 没有 sparse 属性。下一步在 PowerShell 运行：

```powershell
powershell.exe -NoProfile -File "D:\nas\core\scripts\verify-ntfs-sparse.ps1"
```

脚本只创建一个全新的 256 MiB 逻辑容量空文件，核对卷身份、设置稀疏属性并输出分配范围，日志与文件均保留。若执行策略阻止脚本，不修改全局策略，先报告原始错误。若 fsutil 拒绝访问，在管理员终端运行。脚本尚未实际验证；空文件实验通过后仍需验证 ext4 初始化和业务写入后的分配增长。

稀疏空文件已通过（256 MiB 逻辑容量、0 分配块）。在正常 WSL 终端继续：

```bash
cd /mnt/d/nas
sudo bash core/tests/verify-sparse-ext4.sh
```

此脚本只会格式化上述固定实验目录中经全零校验的 blank.img；已有文件系统或非零数据时拒绝运行。成功后在 Windows PowerShell 查询：

```powershell
fsutil sparse queryflag "D:\nas\system\storage\sparse-probe-fb57b053d85447d4b6e0a52ca297471d\blank.img"
fsutil sparse queryrange "D:\nas\system\storage\sparse-probe-fb57b053d85447d4b6e0a52ca297471d\blank.img"
```

将 Linux 挂载日志和 Windows 分配范围一起归档。只有实际分配小于逻辑容量，且内容/权限持久性通过，才认定该平台上的稀疏 ext4 实验通过；跨主机、PostgreSQL 与生产启停仍单独验证。

上述稀疏 ext4 测试现已通过，分配量约 32.56 MiB / 256 MiB。不要再次运行格式化实验；脚本会因非零内容拒绝重复运行。下一步在正常 WSL 终端执行只读盘点：

```bash
cd /mnt/d/nas
bash core/scripts/inspect-runtime.sh
```

如果引擎存在但仅提示 Docker socket 权限拒绝，再使用 sudo bash 重试，并核对 CLI 路径是否改变。该脚本不修复权限或切换引擎，输出中的单项错误须保留。数据库实验前必须明确所连接的引擎及原有数据位置。

## PostgreSQL 隔离实验（当前宿主）

原生 Engine 已在 WSL 安装成功，旧流程中“AI 无法提权/联网”的描述是历史状态。新实验固定使用旧 Compose 的 PostgreSQL 14/VectorChord 镜像摘要，用于兼容存储验证，不代表生产版本已经选定或旧库可自动升级。

```bash
# 普通 WSL 用户执行；每次仅新建一个测试文件，不复用旧实验。
powershell.exe -NoProfile -NonInteractive -File 'D:\nas\core\scripts\new-postgres-probe.ps1'
# 先拉取固定摘要的镜像，再对上一步生成的 database.img 执行：
sudo nsenter -t "$(pidof dockerd)" -m -- bash /mnt/d/nas/core/tests/verify-postgres-storage.sh /mnt/d/nas/system/storage/postgres-probe-<新生成的32位ID>/database.img
```

脚本只接受当前仓库 system/storage/postgres-probe-* 下的 2 GiB 文件，要求初始分配量为零、所有内容为零、没有附加 loop 或旧日志；拒绝旧镜像和重复运行。格式化仅针对这个新文件，不接受物理磁盘路径。创建测试容器前核对固定摘要镜像已在本地，使用 network=none，无端口和重启策略；容器目录显式 bind 到经 ext4/loop 核验的挂载点。

实验写入 10000 行并计算行级聚合校验，生成 pg_dump 自定义格式备份，正常停库后卸载、分离并重新挂载同一文件，验证行级校验，再恢复到第二个测试数据库并核对。正常完成后停止并移除自己创建的测试容器，卸载镜像和分离 loop；保留镜像、备份及全部日志。若停库或卸载失败，不强制卸载，必须按错误位置处理后才可拔盘。不得将本机测试通过外推为跨主机或生产负载已验收。

当前 Codex 会话与 dockerd 使用不同挂载命名空间，因此测试入口显式 nsenter 到 dockerd 的挂载命名空间；脚本会在任何格式化前拒绝不匹配的命名空间。仅在会话内挂载 /tmp 后传给宿主 Docker 会得到 bind source path does not exist；不能因此去创建一个未挂载的数据库目录。


2026-11-15 实测通过：postgres-probe-965558609f4542ddaecb4d218b049854。10000 行停库重挂与逻辑备份恢复校验一致，e2fsck 两次通过。Windows 分配区间与 WSL 统计均为 197.3125 MiB / 2 GiB，sparse 属性保留。容器、挂载和 loop 已收尾，所有实验文件保留。详细版本、校验和及边界见 NAS-PLAN 最新记录；实验不等同于生产部署完成。
