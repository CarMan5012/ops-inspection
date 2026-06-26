# 本地前端资源说明

本目录存放前端运行时需要的第三方静态资源。生产环境和离线环境只引用本目录文件，不访问外部 CDN。

| 文件 | 来源 | 版本 | 用途 | 许可证 |
| --- | --- | --- | --- | --- |
| `modern-normalize.css` | `https://cdn.jsdelivr.net/npm/modern-normalize@3.0.1/modern-normalize.min.css` | 3.0.1 | 统一浏览器默认样式 | MIT |
| `lucide.min.js` | `https://unpkg.com/lucide@0.468.0/dist/umd/lucide.min.js` | 0.468.0 | 后台菜单、按钮、状态和弹窗图标 | ISC |
| `login-rsa-fallback.js` | 项目内置实现 | 1.0.0 | 登录页在 WebCrypto 不可用时进行本地 RSA 加密兜底 | 项目自有 |

如需升级版本，请先下载到本地，再更新本文件中的来源、版本和许可证信息。
