# 目录分工与迁移验收

NAS 的业务目录为 data、core、system，根目录另含 README、LICENSE 等仓库说明文件。NAS 根目录是 Git 仓库根目录，core 是源码目录，内部所有实际文件均维护在根目录的 Git；部署副本、测试结果、账号、数据库和缓存均不得写入 core。

| 目录 | 内容 | 实际位置 |
| --- | --- | --- |
| data | 相册、Git、知识库、共享文件等用户内容 | 数据 SSD，原路径不变 |
| core/web、services | 管理台与自有服务源码、Dockerfile | 数据 SSD，Git |
| core/scripts、tests | 部署与运维工具、验收脚本 | 数据 SSD，Git |
| core/compose、host、templates | Compose、systemd、配置模板和版本锁 | 数据 SSD，Git |
| core/docs | 计划、架构、运维文档 | 数据 SSD，Git |
| system/config、secrets | 实际部署配置与凭据 | 联接到 C:\nas\system |
| system/state、cache | 应用状态、设备身份、模型缓存 | 联接到 C:\nas\system |
| system/databases | Immich 数据库稀疏镜像 | 联接到 C:\nas\system |
| system/logs、backups | 操作日志、历史容器日志、一致性备份 | 联接到 C:\nas\system |
| system/installed、exports | 已安装宿主工具、运行时导出 | 联接到 C:\nas\system |
| system/runtime | 同盘原子恢复所需的暂存、验收产物 | 数据 SSD；local 联接到 C 盘 runtime |
| system/archive | 旧目录和异常 SSD 镜像，保留原件 | 数据 SSD；local 联接到 C 盘 archive |

Linux 的 socket、生命周期锁、ext4 挂载点仍在 /run，不迁移共享 WSL 和 Docker。system 中的数据库、凭据及配置不可公开上传。core 中 .env.example 只有示例；实际 compose.env 留在 system/config。

2026-11-21 迁移前完成一致性备份 20261121-201216；随后正常停止数据库并卸载镜像。用户内容逐项校验哈希和元数据，账号及设备身份校验哈希，数据库镜像在 C 盘内重命名，保留原文件和稀疏分配。未格式化物理盘或重新初始化数据库。

回滚材料位于 system/archive/local/directory-migration-20261121-201206；旧 SSD 文件位于 system/archive/layout-v1-20261121-201206。运行后回滚还须先正常停机并保存最新数据，不得直接覆盖已重新运行的数据库。

验证入口：从 core 执行 python3 -B tests/verify-layout.py；详细本机验收记录在 system/runtime/acceptance。迁移工具、原始日志和私有配置保留在 system/runtime/local，均不进入 Git。

Joplin 用户内容与附件载荷位于 `data/joplin/`，由 joplin 容器独占写入；旧 `data/knowledge/` 保留。Immich 与 Joplin 的数据库共用现有 PostgreSQL 镜像文件，仍位于配置指定的本机 system 目录；不为路径中历史的 immich 名称搬动正在使用的镜像。
