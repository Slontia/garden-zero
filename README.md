# 零号庭院 · Garden Zero

以网页统一管理应用、资源与数据权限，支持多端离线读写和冲突保留的个人 NAS。

零号庭院围绕「自己的数据，自己掌控」组织一套 Windows + WSL2 NAS 环境：用一个管理台连接相册、共享同步盘、文件浏览、Joplin 笔记和 Git，明确每个应用能读写哪些目录，并提供私人远程访问和备份恢复工具。

> 当前是持续开发中的单实例项目，已在现有 Windows + WSL2 主机验证核心功能。新主机一键部署、异机恢复、云端备份尚未完成完整验收。

## 特点

- **单人统一登录**：管理台、文件浏览、相册与笔记共享身份，关联原账户；客户端首次授权后同步，无需分别记忆应用密码。协议差异与应急入口见 [统一登录](core/docs/SINGLE-SIGN-ON.md)。
- **统一控制台**：应用、文件、备份、容器、日志各有独立页面。应用按完整行展示，可展开查看用途、目录权限和子功能；点击状态控制启停。登录不改变原来的运行选择。
- **离线可用的共享同步盘**：设备保存完整本地副本，断网仍可读写，连接恢复后通过 Syncthing 同步。管理台引导绑定本机目录，减少手动复制设备 ID 的操作。
- **冲突可见、历史可追溯**：保留冲突副本和历史版本，支持查看及恢复副本。同步不替代备份，也不自动合并文档内容。
- **数据归属清晰**：维护各数据路径的应用写入者与读取者，按规则核对容器实际挂载。同步盘允许多客户端编辑，由 NAS 上的 Syncthing 统一接收写入。
- **应用与底层容器分开管理**：相册和智能检索属于同一应用；容器页另行展示所属应用、运行时间、CPU、内存和挂载路径。
- **私人远程访问**：使用 Tailscale 和 HTTPS，不依赖公开管理端口。设备需要接入自己的 Tailscale 网络。
- **可检查的存储和备份**：卷身份验证、数据库受控挂载、干净停机、一致性备份及独立恢复检查；独立磁盘增量镜像支持目标校验、断点续传和完整上一版保留，发起备份时可选择是否清理包含删除文件的历史。网盘直接在线读取原始文件，数据库正规在线导出，不生成完整本地副本、不停写应用；支持安全停止和云端历史，采用最终一致性，不承诺数据库与附件来自同一时刻。

## 内置应用

| 应用 | 作用 |
| --- | --- |
| Immich 相册 | 照片与视频管理；智能检索可单独关闭 |
| 共享同步盘 | Syncthing 完整副本同步、设备连接、冲突和版本查看 |
| 网络共享 | SMB 只读挂载；网页浏览已集成在管理台文件页面 |
| Joplin 笔记 | 手机与电脑离线写作，标签、图片及附件同步 |
| Git SSH | 私人 Git 仓库访问 |
| 基础服务 | 管理台、认证与 Tailscale 私人远程连接 |

应用通过 Docker Compose 部署。PostgreSQL 作为共享基础服务，应用使用独立账户和数据库；缓存等专属依赖由对应应用管理，具体镜像定义见 [版本锁](core/templates/versions.lock.json)和 [Compose](core/compose/nas.yml)。

## 目录结构

```text
garden-zero/                  # Git 仓库根目录
├── README.md
├── LICENSE
├── core/                     # 维护的源码、模板、脚本和文档
│   ├── web/                  # 管理台前后端
│   ├── services/             # 自有服务容器入口
│   ├── compose/              # Compose 定义
│   ├── host/                 # systemd 单元
│   ├── templates/            # 非敏感配置模板、权限规则、镜像版本锁
│   ├── scripts/              # 部署、运维、备份与恢复工具
│   ├── tests/                # 验收脚本
│   └── docs/                 # 设计与使用文档
├── data/                     # 用户内容，不提交 Git
└── system/                   # 配置和运行数据，不提交 Git
```

文档以 `D:\nas` 为部署根目录示例。用户内容保留在数据盘，数据库、凭据、缓存和本机备份位于 `C:\nas\system`，通过 `system` 下的目录联接访问。部分同盘暂存和历史归档留在数据盘。共享 WSL、Docker 和 Linux `/run` 不迁入 NAS 目录。详见 [目录分工](core/docs/DIRECTORY-LAYOUT.md)。

## 部署前阅读

当前目标环境：Windows、启用 systemd 的 WSL2、WSL 内原生 Docker Engine 和独立 NTFS 数据盘。同一宿主仅支持一个实例。

```bash
git clone git@github.com:slontia/garden-zero.git /mnt/d/nas
cd /mnt/d/nas
```

克隆源码不会自动创建账号、格式化磁盘或启动服务。已有 NAS 实例不要重复初始化。新环境请按 [源码部署与初始化边界](core/docs/SOURCE-DEPLOYMENT.md)逐步准备；实际配置由准备流程生成，不要直接把 `.env.example` 当作部署配置。

初始化只允许处理经过核验的**全新镜像文件**，不格式化物理盘。Tailscale 授权、客户端依赖安装和设备本地目录选择仍需要使用者参与。浏览器直连本机 Syncthing 受浏览器权限与跨域配置约束；公司 VPN 是否与 Tailscale 共存需要自行验证。

## 已部署实例的日常操作

以下命令从仓库根目录运行：

```bash
bash core/start.sh                      # 恢复保存的应用运行选择
bash core/stop.sh                       # 停止业务，保留管理和远程连接
bash core/stop.sh all                   # 完整停机、卸载数据库，保留运行选择
bash core/scripts/host-command.sh status
bash core/scripts/host-command.sh backup
bash core/deploy.sh --check             # 只读检查部署状态
```

启动命令会等待容器健康，并验证管理台实际登录、首页及宿主状态查询成功后才返回；失败或超时返回非零错误。

Windows 可使用 `core/start.ps1` 和 `core/stop.ps1`；完整停机使用 `core/stop.ps1 -Module all`。拔盘或重启 WSL 前必须确认数据库已正常停止并卸载。

Joplin 的写作界面在客户端，管理台入口用于账户管理。它与 Immich 共用一个 PostgreSQL 实例，使用不同数据库及独立账户。配置、存储与客户端连接见 [Joplin 与共享数据库](core/docs/JOPLIN.md)。

## 开发与验证

AI 协作者请先阅读 [AGENTS.md](AGENTS.md)；用户确认的长期功能约束应同步维护在该文件中。

源码只写入 `core`，实际配置、密钥、测试产物和运行状态写入 `system`。Python 入口使用 `python3 -B`，避免生成字节码缓存。

```bash
python3 -B core/scripts/check-staged.py  # 检查暂存文件中的路径及敏感信息
python3 -B core/tests/verify-app-model.py
python3 -B core/tests/verify-app-capabilities.py
python3 -B core/tests/verify-resources.py
```

部分验收脚本会启停服务、上传测试内容或创建测试镜像，运行前请阅读脚本；不要把整个 `tests` 目录当成无副作用的测试集批量执行。[贡献指南](CONTRIBUTING.md)说明提交边界。

## 文档

- [应用、页面与写入归属](core/docs/APPLICATIONS.md)
- [共享同步盘与文件接入](core/docs/FILE-ACCESS.md)
- [部署与初始化](core/docs/SOURCE-DEPLOYMENT.md)
- [日常运维](core/docs/OPERATIONS.md)
- [增量镜像备份](core/docs/MIRROR-BACKUP.md)
- [阿里云盘与加密云端备份](core/docs/CLOUD-BACKUP.md)
- [备份恢复验收](core/docs/RESTORE-ACCEPTANCE.md)
- [支持环境](core/docs/ENVIRONMENT.md)
- [开发计划](core/docs/NAS-PLAN.md)

## 协议

本项目使用 [Apache License 2.0](LICENSE)。Immich、Syncthing、Tailscale、数据库及其他第三方组件遵循各自的上游协议，项目协议不替代第三方授权。见 [第三方组件说明](THIRD_PARTY_NOTICES.md)。

网页文件浏览位于管理台「文件」页面，复用管理登录，只读浏览与下载。SMB 作为「网络共享」应用独立启停。

### 手动磁盘备份

网页仅管理网盘备份，并统一展示网盘/磁盘备份记录。连接配置的备份磁盘后，在仓库目录执行：

```bash
bash core/scripts/disk-backup.sh
# 仅在明确要清理旧版本时使用：
bash core/scripts/disk-backup.sh --purge-history
```

默认保留历史；清理仅在本次完整提交后执行。卷身份校验、增量更新和中断保护保持生效。

原生 Btrfs 数据卷的迁移顺序、USB 接入与身份保护见 [原生数据卷迁移](core/docs/NATIVE-BTRFS-MIGRATION.md)。当前实例是否已切换以私有配置和迁移验收记录为准；此支持代码不会自动格式化或迁移现有磁盘。
