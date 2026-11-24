# 第三方组件

Garden Zero 编排以下第三方组件；组件本身及其依赖不因本项目采用 Apache-2.0 而被重新授权。

| 组件 | 用途 | 上游 |
| --- | --- | --- |
| Immich | 相册、机器学习及配套数据库镜像 | https://github.com/immich-app/immich |
| Syncthing | 完整副本同步、冲突副本及版本保留 | https://github.com/syncthing/syncthing |
| Tailscale | 私人网络和 HTTPS 访问 | https://github.com/tailscale/tailscale |
| Joplin 客户端 / Server | 离线笔记与私人同步 | https://github.com/laurent22/joplin |
| authentik | 单人身份、OIDC 与 SAML | https://github.com/goauthentik/authentik |
| Caddy | 内部 HTTPS 身份回连 | https://github.com/caddyserver/caddy |
| PyJWT、cryptography、cffi、pycparser | OIDC 签名验证与依赖 | https://pypi.org/ |
| PostgreSQL | 应用与身份共享数据库 | https://www.postgresql.org/ |
| Valkey | 相册缓存 | https://github.com/valkey-io/valkey |
| restic | 外部备份工具 | https://github.com/restic/restic |
| Python | 自有服务运行时 | https://www.python.org/ |
| Git、OpenSSH、Samba | 仓库访问和文件共享 | 各组件官方发行包 |

具体镜像及摘要见 `core/templates/versions.lock.json`，系统包见各 Dockerfile。使用、再分发或发布包含这些组件的镜像时，请核对所选版本及其依赖的许可证，保留所需声明并履行相应义务。本表是组件索引，不是全部传递依赖的许可证清单。

Joplin 客户端与 Server 的授权不同。所选 Joplin Server 版本采用上游 Server 专用个人使用许可，不能将其视为客户端 AGPL 授权；本实例为个人自托管使用，源码编排不重新授权上游软件。部署商业服务或再分发前阅读该版本 `packages/server/LICENSE.md`。
## 网盘备份组件

OpenList 使用 AGPL-3.0 协议，源码及修改义务以 [OpenList 仓库](https://github.com/OpenListTeam/OpenList)为准；本项目调用官方容器 API，不修改其源码。rclone 使用 MIT 协议，Restic 使用 BSD-2-Clause 协议；二维码生成库 python-qrcode 使用 BSD 协议。各组件的协议与发行资料随其上游分发，本项目 Apache-2.0 不替代第三方授权。
