# ETF 资金观察

通过网页、微信小程序或小红书小程序观察 A 股宽基指数、ETF 申赎和个股资金贡献。首页先显示所选宽基指数的区间资金，再下钻到场内 ETF 和场外披露。个股页按可用持仓权重估算 ETF 资金贡献，可叠加不复权收盘价。红色表示流入，绿色表示流出。

网页使用 Astryx 的 Neutral 主题、导航与工作区组件。微信和小红书使用原生组件，并在构建时转换相同口径的颜色、间距与字号。在已有完整数据的 Windows 电脑上，可双击[打开 ETF 资金观察](打开ETF资金观察.cmd)进入网页。

## 从源码开始

以下命令从仓库根目录运行。建议使用 Node.js 24、npm 和 Python 3.11 以上。网页与小程序可先独立构建；数据库、行情采集和手机平台账号在接入真实数据或正式发布时配置。

| 目标 | 命令或入口 |
| --- | --- |
| 安装并构建网页 | `npm --prefix web-trial ci`，再运行 `npm --prefix web-trial run build` |
| 打开源码预览 | 运行 `npm --prefix web-trial run preview`，浏览器打开 `http://127.0.0.1:4175/`。尚未接入快照时页面显示数据未发布 |
| 运行网页测试 | `npm --prefix web-trial test` |
| 生成两个小程序联网调试工程 | `node mini-programs/build.mjs --mode local`，在微信或小红书开发者工具导入对应的 `mini-programs/dist/wechat` 或 `mini-programs/dist/xiaohongshu` |
| 检查两个原生工程的模板和样式 | 先运行 `npm --prefix work/miniapp-validation ci`，生成上述调试工程后运行 `node mini-programs/tests/host-compile.mjs` |
| 准备数据服务环境 | 在 Python 虚拟环境中执行 `python -m pip install -r server/requirements.txt`。依赖 Scrapling 的数据源见[采集说明](server/history/INDEX_SOURCES.md) |

完整数据保存在数据所有者的运行环境。客户端按可用日期显示数据，保留迟报、停牌和未披露缺口。范围左端是查询起点，实际覆盖取决于该基金或股票的上市时间及来源披露。场外基金按报告期显示申购和赎回份额，个股资金贡献属于估算。

## 接入已有数据

1. 在已授权机器上，将本人的已验证网页快照和选择文件恢复到 `web-trial/.local/`。保留 `local-view.json` 引用的各版本目录、清单和摘要，已有股价缓存保留原有目录结构。初次采集或重新生成快照时，按[历史仓库流程](server/history/README.md)以及[网页更新流程](web-trial/README.md)核验后切换。
2. 网页构建后运行 `node server/scripts/local_web.mjs`，在同一台电脑打开 `http://127.0.0.1:4173/`。同机开发者工具构建的 local 包也使用这个数据服务。手机真机使用两平台允许的私人 HTTPS 域名和单设备配对，接入操作见[小程序说明](mini-programs/README.md)。
3. 要生成带数据的双端离线包，先安装上述原生编译依赖，在 `mini-programs` 内运行 `python scripts/release.py prepare`。只有两端均通过检查，本机离线包入口才会切换。原始数据库和离线发行包由数据所有者单独保管。

## 维护入口

- [历史库与覆盖口径](server/history/README.md)和[沪深指数及场内外基金来源](server/history/INDEX_SOURCES.md)。
- 网页 UI 使用 Astryx React 组件和中性主题，入口在 [web-trial/ui/dashboard.mjs](web-trial/ui/dashboard.mjs)。原生端在 [mini-programs/src](mini-programs/src) 维护适配微信和小红书的模板。
- [股票收盘价来源与维护](server/history/STOCK_PRICE_SOURCES.md)。
- [微信与小红书更新手册](mini-programs/UPDATES.md)涵盖数据更新、程序版本、分别提审及回退。GitHub 提交保存源码版本，手机上线版本以相应开发者后台为准。
- `miniapp/`、`model/` 和 `docs/` 保存早期模型和客户端。当前双端小程序的源码位于 `mini-programs/`。

随代码保留了 [tiny-inflate 许可](mini-programs/src/lib/vendor/LICENSE)及[旧微信图表组件的第三方声明](third-party/README.md)。

## 后续源码交付

每次完成界面、计算或客户端更新，维护流程会把已验收的源码版本同步到 [项目私有仓库](https://github.com/aHao030/etf-flow-miniapp)。电脑和两端小程序的数据更新仍依照交易日历运行。

在根目录使用 Python 3.11 以上、Git、GitHub CLI 和“从源码开始”中对应的开发环境。GitHub CLI 可使用已登录账号，也可读取 Git Credential Manager 中此仓库的既有授权。把本次已审查的修改提交后，运行以下命令。连续多次本地提交可在同一条命令中按顺序列出完整 SHA。

```powershell
python tools/publish_source.py --commit (git rev-parse HEAD)
```

工具读取指定提交，在临时目录验证源码及受影响的测试，核对私有仓库和远端 `main` 后推送。返回的 `commit` 对应 GitHub 上已核实的源码。重复运行同一版本可以直接确认提交号。若同事已更新远端分支，先核对并整合其变更再重试。手机体验、提审与上线分别由各自平台处理。
