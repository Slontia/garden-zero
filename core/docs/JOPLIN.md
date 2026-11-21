# Joplin 与共享 PostgreSQL

## 使用方式

在手机、Mac 或 Windows 上安装官方 Joplin 客户端。NAS 上的 Joplin Server 提供同步、账户与笔记分享，不是完整的博客网页编辑器。客户端支持离线编辑、Markdown、标签及图片；草稿可用笔记本或标签组织。手机同步时需要保持 App 在前台；连接私人 NAS 时还需 Tailscale 可达，不承诺锁屏后持续后台同步。

客户端「设置 → 同步」选择 Joplin Server，地址使用 Meta 的 Joplin 详情所示 HTTPS 地址。当前沿用旧知识库的私人 8444 入口与本机 8082 端口，无需增加访问控制端口。初始日常同步账户及密码在 `C:\nas\system\secrets\joplin-user.json`，管理员账户在相邻 `joplin-admin.json`。不要将这些文件提交到 Git 或发送到聊天。更改密码后更新自己的客户端；自动启动不会强制重置用户修改后的密码。

浏览器入口是 Joplin 账户管理。旧 `data/knowledge/` 原样保留，后续可由客户端导入 Markdown；没有自动导入、删除或覆盖。

## 部署结构

- 一个 `database` 容器，沿用当前已验证 PostgreSQL 镜像、主版本和 Immich 向量扩展，不新建第二个生产 PostgreSQL。
- `immich` 账户只连接 `immich` 数据库，`joplin` 账户只连接 `joplin` 数据库。两个账户均无超级用户、建库、创建角色权限；禁止 PUBLIC 跨库连接。管理员 `postgres` 只用于宿主维护、扩展与备份，不提供给业务容器。
- 业务密码分别存于 `system/secrets/immich-db-password` 与 `joplin-db-password`，环境文件分别为 `immich.env` 和 `joplin.env`。不复用管理员密码。
- PostgreSQL 属于受保护基础服务。模块名为 `postgres`；相册模块只管理相册与缓存，笔记模块沿用配置键 `knowledge`，实际容器为 `joplin`。停止一个业务不停止数据库；完整停机先停业务，再停数据库并卸载镜像。
- 共享数据库准备使用短范围依赖锁；各应用就绪等待仍可并发，不把整个启动过程重新串行化。
- Joplin 启用文件系统载荷存储：笔记序列化内容、标签内容和附件由 Joplin 管理在 `data/joplin/`；关系、同步元信息等位于数据库。内部文件不是用户可直接修改的 Markdown 文件夹。
- 数据库仍使用配置指定的受控 ext4 稀疏镜像。已有路径即使含 `immich` 字样也不为改名移动数据库；界面按共享数据库展示容量。

新环境在 `prepare-services.py` 生成独立密码，启动共享数据库后由 `nas_database.provision` 创建角色、库和扩展，随后启动业务。现有管理员连接环境会被默认准备流程拒绝，必须先备份、暂停写入并显式迁移，不能用初始化脚本覆盖。当前实例迁移前已保留完整快照和旧环境文件；记录位于 `system/state/shared-postgres-migration/`。Immich 的未来向量扩展升级需管理员预先维护，不能临时授予业务账户超级用户来绕过隔离。

Joplin 的初次账户初始化替换上游默认管理员密码，并建立普通同步用户。首次初始化完成后不重置账户。容器只监听本机端口，通过现有私人 Tailscale Serve 访问，不启用公网 Funnel。健康检查携带 APP_BASE_URL 的域名，避免 Joplin 的来源校验误报故障。

## 备份与恢复

新一致性快照采用 schema 3，保留旧版可读字段，并增加每个数据库的 dump 文件和表行数。备份持有生命周期排他锁，暂停 Immich、Joplin、Git 与共享同步的写入后，保存：

- `database.dump`：Immich 数据库。
- `joplin.dump`：Joplin 数据库。
- `database-roles.sql`：角色定义，不包含密码哈希。
- `user-content.tar`：包括 Joplin 文件载荷在内的全部用户数据。
- `configuration.tar`：环境、凭据和恢复配置；其中凭据属于敏感备份内容。

只有全部完成并校验后才写 COMPLETE、提交快照；最后恢复此前运行的业务。增量镜像的 recovery 目录按清单复制所有恢复载荷，不能漏掉 Joplin dump 或角色文件。原有中断恢复、历史保留和提交后清理语义不变。

`restore-check.py` 与 `recover-database.py` 兼容旧 Immich 单库快照和新双库快照。新恢复先建立角色，再恢复两个数据库，逐库核对表行数，并恢复匹配的用户文件和配置。无密码角色定义需结合备份的独立密码，通过受控准备重新设置；不要只恢复数据库却丢失文件载荷。隔离恢复检查不切换生产数据库，也不等于完整异机灾难恢复验收。

## 验证入口

- `verify-shared-lifecycle.py`：隔离夹具验证生命周期、卸载保护与凭据迁移边界。
- `verify-shared-postgres.py`：会在当前实例创建并删除一条临时同步笔记；验证各角色正常连接、跨库和错误密码拒绝、非管理员同步上传与读取、相册核心表行数。不是纯只读测试。
- `verify-meta-status.py` / `verify-meta-browser.ps1`：四页、Joplin 路径、基础服务与桌面手机布局。
- `restore-check.py <完整快照路径>`：创建独立临时数据库实例验证两个库的恢复。该实例仅供验收，结束后清理，不是第二套生产数据库。

上游参考：[Joplin Server 安装与存储](https://github.com/laurent22/joplin/blob/dev/packages/server/README.md)、[Immich 使用已有 PostgreSQL](https://docs.immich.app/administration/postgres-standalone/)、[Joplin 手机同步限制](https://joplinapp.org/help/faq/#not-all-notes-folders-or-tags-are-displayed-on-the-mobile-app)。实际部署镜像以版本锁为准。
