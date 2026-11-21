# 源码部署检查与初始化边界

当前支持 Windows + WSL2、原生 Docker Engine、一个 NAS 实例。宿主专属目录固定在 C:\nas，项目名、服务端口和 systemd 服务仍是单实例约定。共享 WSL 和 Docker 不迁移。当前实例已经初始化，不能重跑新建流程。

## 本轮已验证

- 从候选公开源码创建独立临时 Git 仓库，再实际 clone 到另一目录；不对当前工作仓库提交或推送。
- Git 待提交路径、体积、私钥及常见令牌检查；clone 后 Python、Shell、PowerShell 语法检查。
- 克隆的控制器发现配置属于另一个目录，立即拒绝控制现有 NAS。
- Windows 准备入口从自身所在目录推导盘符和 NTFS 卷 GUID，不复制本机固定磁盘标识；`-CheckOnly` 不创建文件或目录、不修改 ACL/联接、不创建或格式化镜像。
- Windows 新建入口若发现 C:\nas 已属于另一个 checkout，在修改任何文件前拒绝。
- 初始化预检核对 Windows 准备记录、实际卷身份、部署目录和新镜像；拒绝旧配置、旧密钥、错误卷、其他目录的准备记录、未完成初始化记录，以及已有分配块的镜像。
- 正向预检使用隔离的 64 GiB 空白稀疏测试文件，只调用计划函数，不调用 mkfs；测试文件随后已删除。生产配置和凭据指纹保持一致。

验收脚本：`core/tests/verify-source-clone.py`。本机证据：`system/runtime/acceptance/source-clone.json`。运行会在忽略目录保留独立源码副本与脱敏报告；不会启动第二个 NAS 或提交当前仓库。

## 新环境准备入口

以下流程仅说明目前已有的准备/初始化接口，不代表完整部署已能一键执行。在新主机正式部署前，仍需完成下节列出的安装流程与完整验收。

1. 将源码放在 NAS SSD 的 NTFS 卷上，不能放在 C: 或网络共享路径。先在 PowerShell 执行只读检查：

```powershell
.\core\scripts\prepare-windows-host.ps1 -CheckOnly
```

2. 核对输出的 checkout、盘符、NTFS 与卷身份。只有确认是全新环境，才执行 `-CreateDatabaseImage`；可额外提供 `-ExpectedVolumeGuid` 锁定选定卷。准备工具默认仅创建排他新建的 `C:\nas\system\databases\immich\postgres.img`，不格式化物理盘。Windows 准备记录放在 `C:\nas\system\state\prepared-host.json`，禁止照抄其他主机记录。
3. WSL 中先执行只读预检，成功才允许正式初始化：

```bash
sudo -n nsenter -t "$(pidof dockerd)" -m -- python3 core/scripts/initialize.py --dry-run
```

4. 正式初始化只接受准备好的全新、64 GiB、零分配、单硬链接普通镜像文件，先落下排他初始化标记，再在镜像文件内创建 ext4。凭据排他创建；中断后需要检查现场，不自动重试覆盖。现有生产实例预检会明确拒绝，这是预期保护。

## 当前可执行的安装链路

完成 Windows 准备和安全初始化后，`bash core/deploy.sh --check` 默认只读检查。新环境的后续安装入口为：

```bash
bash core/deploy.sh --apply --build --fetch --start --register-login
```

它构建本地镜像、仅按摘要拉取远端镜像、写入本机环境、预生成带认证且零同步目录的 Syncthing、保留既有凭据与 Git 密钥、安装源码中的 systemd/宿主工具并核对登录任务。已有不同文件会拒绝覆盖，经过审查的宿主工具更新使用 `--allow-host-update`。当前实例已实际执行安装入口并通过隔离服务准备验证，不应为重复验收重新初始化。

默认数据库文件在 C:\nas\system\databases\immich，准备记录写入明确的 `image_wsl`；初始化仅接受固定内部路径下全新的镜像文件；当前 USB 主机不支持在 SSD 上运行数据库。只读预检和新文件防覆盖原则保持不变。Tailscale 新节点仍需用户登录授权；已配置节点再次运行远程配置会保留验收记录，并把域名同步到实际 Meta 环境。

## 尚未完成

完整新主机从零启停/恢复未通过，步骤 37 保持部分验收。固定端口/项目/控制 socket 不支持同宿主第二实例并行。Linux 适配、异机接续及长期负载分别验收。

Docker daemon 的 WSL NAT 代理已按启动时网关持久配置；实际固定摘要 GHCR 拉取通过，Docker Hub 端点仍出现 TLS EOF，尚待网络排查。已有缓存镜像运行正常，不能把缓存齐全当作新机可下载全部依赖。离线更新管理镜像可显式使用已验证的本地 `NAS_WEB_BASE_IMAGE`，默认 Dockerfile 仍固定 Python 基础摘要。


### 完整同步空间

当前用户选择多设备完整同步，初始化默认 file_access.mode=full_sync，避免重新开放独占 SMB 写入。首次应用启动后需显式执行共享准备：停止 nas-agent，运行 `sudo -n python3 core/scripts/configure-sync.py --apply`，再启动 nas-agent。该步骤准备 shared、版本保留和私人 22000 传输，Mac 安装/设备配对与本地位置仍由客户端完成。已有配置只通过显式迁移，不由 initialize 覆盖。仅对已验证的当前 NAS 实例声明结果；完整新机部署验收仍未完成。
