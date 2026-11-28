# 目录分工与迁移验收

NAS 的业务目录为 data、core、system，根目录另含 README、LICENSE 等仓库说明文件。NAS 根目录是 Git 仓库根目录，core 是源码目录，内部所有实际文件均维护在根目录的 Git；部署副本、测试结果、账号、数据库和缓存均不得写入 core。

| 目录 | 内容 | 实际位置 |
| --- | --- | --- |
| data | 相册、Git、笔记、共享文件等用户内容 | 原生 Btrfs SSD 的 `data` 子卷；应用实际路径 `/run/nas/storage/data` |
| core/web、services | 管理台与自有服务源码、Dockerfile | `C:\nas\core`，Git |
| core/scripts、tests | 部署与运维工具、验收脚本 | `C:\nas\core`，Git |
| core/compose、host、templates | Compose、systemd、配置模板和版本锁 | `C:\nas\core`，Git |
| core/docs | 计划、架构、运维文档 | `C:\nas\core`，Git |
| system/config、secrets | 实际部署配置与凭据 | `C:\nas\system` |
| system/state、cache | 应用状态、设备身份、模型缓存 | `C:\nas\system` |
| system/databases | 共享 PostgreSQL 数据库稀疏镜像 | `C:\nas\system`，维持现有镜像与 UUID |
| system/logs、backups | 操作日志、历史容器日志、本机恢复点 | `C:\nas\system` |
| system/installed、exports | 已安装宿主工具、运行时导出 | `C:\nas\system` |
| system/runtime、archive | 受保护的临时文件、验收产物、历史归档 | `C:\nas\system` |

仓库根目录为 `C:\nas`；`data` 符号链接只用于 WSL 运维浏览，不是应用挂载源，缺盘时保持不可访问。Btrfs 顶层挂载 `/run/nas/storage` 的 UUID、PARTUUID、分区容量和活动子卷均受保护。旧 `host` 目录属于忽略的历史运行数据，不作为源码提交。

共享历史恢复把临时载荷放在 `.stversions` 中，通过已打开的目录句柄创建及发布，保持与恢复目标处于同一文件系统和子卷；不再假设 `system/runtime` 与用户文件同盘。新副本继承共享目录的 UID/GID，避免原生 POSIX 权限阻止同步进程读取。

Linux 的 socket、生命周期锁、ext4 挂载点仍在 /run，不迁移共享 WSL 和 Docker。system 中的数据库、凭据及配置不可公开上传。core 中 .env.example 只有示例；实际 compose.env 留在 system/config。

以下为旧布局的迁移记录，实际路径以上表及当前配置为准。

2026-11-21 迁移前完成一致性备份 20261121-201216；随后正常停止数据库并卸载镜像。用户内容逐项校验哈希和元数据，账号及设备身份校验哈希，数据库镜像在 C 盘内重命名，保留原文件和稀疏分配。未格式化物理盘或重新初始化数据库。

回滚材料位于 system/archive/local/directory-migration-20261121-201206；旧 SSD 文件位于 system/archive/layout-v1-20261121-201206。运行后回滚还须先正常停机并保存最新数据，不得直接覆盖已重新运行的数据库。

验证入口：从 core 执行 python3 -B tests/verify-layout.py；详细本机验收记录在 system/runtime/acceptance。迁移工具、原始日志和私有配置保留在 system/runtime/local，均不进入 Git。

Joplin 用户内容与附件载荷位于 `data/joplin/`，由 joplin 容器独占写入；旧示例目录 `data/joplin-legacy/` 已按用户要求清理。Immich 与 Joplin 的数据库共用现有 PostgreSQL 镜像文件，仍位于配置指定的本机 system 目录；不为路径中历史的 immich 名称搬动正在使用的镜像。
