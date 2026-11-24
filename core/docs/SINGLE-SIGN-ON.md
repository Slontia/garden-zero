# 单人统一登录

零号庭院采用 authentik 提供单人身份：日常登录只输入密码，内部固定为 owner。首次部署沿用原管理密码，身份服务创建后不会因重复配置重设密码。管理台与文件浏览使用 OIDC，Immich 使用原生 OIDC，Joplin 使用原生 SAML；这不是给各应用设置相同密码。

## 使用

从原管理地址进入，登录后打开文件浏览、Immich 和 Joplin，无需分别输入应用密码。管理台的 Joplin 入口直接进入组织账户登录；直接打开 Joplin 的普通登录页面时，选择组织账户登录。

手机、电脑上的每个客户端仍需首次授权，客户端与浏览器的会话分别保存：

- Immich：填写原相册地址，选择零号庭院登录；由系统浏览器完成身份授权后返回应用。保留 `app.immich` 回调及服务端 mobile redirect，不能把它们改成任意重定向通配符。
- Joplin：同步目标选择 **Joplin Server（SAML）**，填写原笔记服务器地址，按客户端提示打开浏览器、登录，并完成授权码交换。有些客户端版本需要复制一次性授权码；不再使用原笔记账户密码。
- Joplin 原普通账户原地转换为外部身份账户，用户 ID、笔记与附件保留。此版本不允许同一账户同时使用本地密码和 SAML。活跃记录清空旧密码字段，以兼容该版本的一次性授权码保存逻辑；原登录字段在私有回退记录和切换前备份中保留。独立本地管理员账户仍保留。
- Immich 原账户按已核验邮箱关联，禁止自动注册新用户。首次进入仍可能出现应用自身尚未完成的主题等引导。

不同浏览器、隐私窗口、手机应用内浏览器可能不共享身份会话，因此首次授权仍可能再输入统一密码。手机后台同步、锁屏行为由客户端和手机系统决定。

Git 继续使用 SSH 公钥，Syncthing 继续设备配对，SMB 使用协议专用凭据；这些入口不支持浏览器 OIDC/SAML，不能将它们描述成已接入统一登录。

## 结构与边界

认证归属 platform 基础服务，不增加首页应用行。新增 authentik server、worker 与内部 HTTPS 网关三个容器。仍只有一个生产 PostgreSQL，新增 `platform_auth` 数据库及同名非超级用户角色，与原应用数据库隔离。身份服务运行状态放 system，不写 data。

对设备的访问仍走私人 Tailscale Serve，新增同域 `/auth/` 路径，不开放 Funnel，不新增 tailnet 端口。Docker 网络内的域名别名指向 Caddy，使后端可以验证同一个 HTTPS issuer；其宿主端口仅绑定 loopback，不能改成 `0.0.0.0`。Caddy 使用 Tailscale 签发的真实证书，不关闭 TLS 校验。nas-agent 每日检查证书，变更时重新加载网关，失败进入运维审计并按小时重试；主机长期离线后应先检查证书再使用。

自有 OIDC 入口校验浏览器绑定的 state、PKCE、nonce、RSA 签名、issuer、audience、有效期和唯一 owner subject。身份提供方使用固定用户密码流程及失败声誉限制，应用提供方仅授权 owner，关闭开放注册。Meta 本地密码入口继续保留原有请求头保护、失败限速及会话吊销。

管理台退出会吊销本地会话，并进入身份提供方的退出流程，不把 ID token 放入 URL，退出完成后进入登录入口；不改变应用启停。**它不保证已登录的第三方应用、其他浏览器或手机同步会话同时退出。** 停用设备时应同时撤销对应应用会话或 Tailscale 设备授权。不能把 SSO 退出描述成全局远程擦除。

## 应急与回退

认证服务不可用时，管理台及文件浏览的 `/login/local` 使用原本地管理密码；它是独立应急密码，后续在身份服务修改统一密码不会自动改写此密码。原 Immich 本地密码登录保留。Joplin 普通 `/login` 保留独立管理员入口，凭据在主机 `system/secrets/joplin-admin.json`；不要用管理员做日常同步。身份后台的原默认认证 flow 可用于恢复管理员，凭据在 authentik-admin-password 中；日常 owner 不拥有身份后台管理权限。

切换前生成完整备份和私有 `system/secrets/sso-rollback.json`。回退登录方式可运行：

```bash
sudo -n python3 -B core/scripts/configure-sso.py --rollback
```

回退保留用户内容与 ID，恢复 Joplin 本地认证标志及此前认证配置，重新加载相关服务。它不是数据库版本回退，不能替代完整灾难恢复。不删除身份数据库或已有备份。脚本要求既有应用账户、本地应急凭据仍可用；切换中途失败要检查私有日志，不能重复初始化账号。

现有实例的接入分为 `nas_sso.py` 基础准备、安装宿主编排、启动 identity、`configure-sso.py` 提供方配置、`configure-sso.py --activate` 切换入口。运行顺序、既有备份、TLS 与账号关联须先核验；不能把这些步骤当作未经审查的新机一键部署。

## 备份与验收

完整备份同时覆盖三个数据库、独立角色、身份配置、密钥、证书及运行状态。恢复检查接受 `platform_auth` 并逐表核对；外部镜像沿用完整备份 manifest，不遗漏身份数据库。配置压缩包包含恢复所需秘密，不能公开。

`verify-oidc.py` 在隔离容器验证错误签名、issuer/audience/owner/nonce、跨浏览器回调、过期及重放等拒绝行为。`verify-sso-browser.ps1` 使用独立 Chrome profile 验证一次密码跨应用登录、既有相册用户 ID、Joplin 一次性客户端授权码。其临时会话仅保存在主机私有 secrets，随后运行 `verify-shared-postgres.py` 验证跨库拒绝与笔记同步往返，并撤销测试会话。

真实 iPhone、Android 应用首次登录仍需使用者安装后验收；浏览器及协议测试不等于真实手机客户端通过。

上游参考：[Joplin SAML 配置](https://joplinapp.org/help/apps/server/saml/)、[Immich OAuth](https://docs.immich.app/administration/oauth/)、[authentik 文档](https://docs.goauthentik.io/)。版本以仓库镜像锁为准。
