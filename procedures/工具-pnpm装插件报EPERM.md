# 工具-pnpm装插件报EPERM

> **触发**：`plugin_manager` 装插件失败，报 `ERR_PNPM_EPERM` / `[importPackage ...] EPERM: operation not permitted, rename '...\<包>_tmp_xxxx' -> '...\<包>'`
> **验证**：对报错里那个包目录跑 `Rename-Item`，报「访问被拒绝」；但目录内能新建文件、ACL 是 FullControl → 判定为**句柄占用**，不是权限

## 首选

1. 记住报错里的那个包目录（本次是 `@tt-a1i/archify-dsh`）
2. 停掉占用它的 bundle：
   `plugin_manager { action: set_bundle, target: "<包名>", enabled: false }`
3. 重试 `install_bundle`
4. 装完开回来：`set_bundle <包名> enabled=true`

## 被挡时

- 占用方是 shipped 插件、或 `set_plugin` 返回 `unaddressable`（bundle 自己 insert 的行改不了）→ 只剩重启 DSH 一途
- 别改用 `link:` 规格绕过：pnpm 添加依赖时仍会整体重导已有包，照样撞

## 别再来

| 弯路 | 为什么不通 |
| --- | --- |
| 重试几次 | 稳定复现，不是瞬时占用 |
| 怀疑 ACL / 权限不足 | 目录内可写、ACL 是 MSN FullControl；只有 rename 失败 = 句柄 |
| 跑 install.ps1 或手改 profile | 底层还是同一个 pnpm 重导，一样卡 |
| 换 `file:` / `link:` 规格 | 同上 |

## 注意

- 本机 **archify 会挂一个 skill 文件监视器（chokidar）盯着自己的 package 目录**，只要它在跑，任何触发整体重导的 pnpm 操作都会卡在那个目录。
- 停 bundle 会让它的 skill 从清单里暂时消失（本次实测消失又回来了）；装完必须开回来，否则 archify 技能就没了。
- 副作用：重新组合时 `dsh-longtou` 可能因旧 fiber 的 `/dsh-longtou/panel.js` 路由未释放而 `failed`，**重启 DSH 即恢复**、且不会复发。

证据：2026-10-19 装 `dsh-power-menu`，连续 3 次 EPERM（install / 重试 / link: 规格）；停 archify 后一次通过。
