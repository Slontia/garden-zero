# 第三方组件

Garden Zero 编排以下第三方组件；组件本身及其依赖不因本项目采用 Apache-2.0 而被重新授权。

| 组件 | 用途 | 上游 |
| --- | --- | --- |
| Immich | 相册、机器学习及配套数据库镜像 | https://github.com/immich-app/immich |
| Syncthing | 完整副本同步、冲突副本及版本保留 | https://github.com/syncthing/syncthing |
| Tailscale | 私人网络和 HTTPS 访问 | https://github.com/tailscale/tailscale |
| PostgreSQL | 相册数据库 | https://www.postgresql.org/ |
| Valkey | 相册缓存 | https://github.com/valkey-io/valkey |
| restic | 外部备份工具 | https://github.com/restic/restic |
| Python | 自有服务运行时 | https://www.python.org/ |
| Git、OpenSSH、Samba | 仓库访问和文件共享 | 各组件官方发行包 |

具体镜像及摘要见 `core/templates/versions.lock.json`，系统包见各 Dockerfile。使用、再分发或发布包含这些组件的镜像时，请核对所选版本及其依赖的许可证，保留所需声明并履行相应义务。本表是组件索引，不是全部传递依赖的许可证清单。
