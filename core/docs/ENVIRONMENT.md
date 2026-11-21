# 支持环境与部署边界

当前目标是 Windows + WSL2（启用 systemd）和 WSL 内原生 Docker Engine。只支持同宿主单个 NAS 实例；固定端口、项目名和控制 socket 不支持并行第二实例。

- NAS 工作目录位于单独的 NTFS 数据盘；文档用 D:\nas 举例，准备脚本从实际路径读取卷身份。
- 用户内容留在 data；运行配置、凭据、模型缓存、数据库稀疏镜像和本机备份位于 C:\nas\system。
- system 使用 Windows 目录联接提供统一入口；同盘原子恢复暂存和旧 SSD 归档留在数据盘。
- PostgreSQL 运行在内部盘上的 ext4 镜像中，不直接使用 NTFS 数据目录。默认镜像逻辑大小 64 GiB，实际占用随数据增长。
- WSL 与 Docker 是共享基础设施，其安装和运行数据留在 Linux 默认位置。
- 启用机器学习时，WSL 至少需要 5 GiB 可见内存；模型加载和大批处理仍需评估余量。
- 远程入口使用用户自己的 Tailscale 网络和设备地址，不应复制其他部署的 GUID、账号、密钥或设备 ID。

这份说明不代表 Linux、macOS 服务端或所有 Windows 硬件已经验收。新主机流程见 [源码部署](SOURCE-DEPLOYMENT.md)。
