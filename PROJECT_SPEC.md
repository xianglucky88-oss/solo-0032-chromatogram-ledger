# 色谱批次核算台规格

## 用户与数据

实验分析员负责导入批次并追踪每个组分的核算结果。数据复核员查看输入、峰、曲线、前序质控和封存件之间的对应关系。输入是已提取的峰表和方法参数，适用于示例中使用结构化数据的小型实验室工作流。

- 方法包含 ID、修订号、定位峰窗口、组分窗口、组分空白限值、最小 R² 和核查误差阈值。同一方法定义内的窗口互斥。
- 批次包含方法和顺序固定的进样。按 `order` 确定进样顺序。进样分为标准样、空白、核查样与样品。列出的标准样必须组成序列的开头区段。每一列出的标准样和核查样须为每个被测组分提供标称浓度。样品记录样品 ID 和稀释倍数。
- 单次进样中的峰有唯一 ID、实测保留时间、峰面积和饱和标记。双文件模式的 CSV 列名为 `injection_id,peak_id,time,area,saturated`，由进样 ID 并入序列。

## 处理规则

1. 双文件导入先关联峰和进样，随后与完整 JSON 共用字段校验、数值边界、规范化和 SHA-256。输入摘要涵盖进样顺序。同批次 ID 与同摘要重复导入可复用已有记录。资料冲突返回 HTTP 409。
2. 每次进样选取一个满足窗口与面积门槛的定位峰，以其实测保留时间偏移校正该次进样。组分窗口按校正后保留时间独立匹配。缺峰、饱和、歧义和无法定位都保留原因。
3. 对每个组分分别建立带截距的一次最小二乘曲线，至少三个不同浓度水平。只有唯一且未饱和的匹配峰参与拟合。拟合必须满足正斜率和方法规定的 R²，标样浓度范围作为定量范围。
4. 对每个组分独立跟踪当前进样之前最近的空白与核查样。对应的证据不合格或缺失会阻止该组分的交付。序列后续证据只影响后续进样。
5. 样品组分反算浓度，检验量程，应用稀释倍数。样品同次进样内的组分可分别处于可交付或暂缓状态。进样证据包含窗口、候选峰以及判定引用的前序空白与核查样编号。批次报告同时提供标准曲线和全部进样的核算结果。
6. 封存以事务持久化确定性的 JSON 与摘要。重复封存返回已有记录。CSV 与 JSON 都由同一冻结内容导出，仅包含可交付组分。有相同方法正文及引擎版本的两批结果可以按样品、进样与组分比较。
7. 总台工作站归档接收按收件时确认的固定工作站标识与原批次号共同认定一条记录，标识不从备份读取。每份 SQLite 备份是一次收件：只读打开，其中每条批次都由该站原输入重算输入摘要与报告，封存件由原输入、报告重新生成后再与备份内的文件和摘要比对；仅内部摘要互相一致的文件不能入档。任一条核验失败或与已收资料冲突，整份收件在一个事务内拒收，不留任何痕迹。
8. 原样重交按备份文件字节的 SHA-256 识别并复用收件记录。较早副本晚到只做幂等核对，不撤已收封存，也不删除该工作站的其他批次；旧备份尚未封存而后续备份已封存的同号批次，由后续备份补齐封存。同一工作站同号输入发生变化返回 `archive_input_conflict`，已入档封存出现不同内容返回 `archive_snapshot_conflict`，均为 HTTP 409。不同工作站的同号批次互不影响，即使输入与封存完全相同也是各自独立的记录。
9. 归档数据保存在独立的归档 SQLite 中，与本机批次库物理分开；总台按来源工作站打开原输入、报告和进样证据，下载原样封存件并复算原摘要，源备份移走后这些操作仍可完成。没有参加归档接收的本机批次仍沿用原来的批次号入口和封存流程。

## 代码边界

- `chromaledger/sequence.py`、`method.py`、`peaks.py` 分别解释进样序列、方法与单次进样峰表。`peak_csv.py` 处理异构输入关联，`intake.py` 调用规范化和入库。
- `alignment.py` 与 `assignment.py` 实现定位和归属。`calibration.py` 拟合曲线。`blank.py`、`quality.py` 与 `eligibility.py` 分别执行空白、核查和样品规则，`analysis.py` 编排有序数据流。
- `repository.py` 提供记录访问，`snapshots.py` 持久化封存，`exports.py` 生成下载件，`evidence.py` 提供证据读模型。`integrity.py` 从只读连接独立重算数据库中记录的批次、封存件与归档库。
- `archive_database.py` 定义归档库结构，`archive_verify.py` 只读核验工作站备份并从原输入复算全部资料，`archive_intake.py` 在单事务内完成冲突检查与入档，`archive_views.py` 提供归档读模型，`archive_routes.py` 暴露归档只读接口。`tools/intake_archive.py` 是总台命令行收件入口。
- `web/js` 使用原生 ES 模块，分别处理导入、目录、序列、曲线图、峰图、进样证据、交付和工作站归档。`web/css` 分为样式基底、工作台布局和证据视图。页面是 `/`。

## 本机 API

预检与导入可提交完整批次 JSON，或提交 `format` 为 `peak-csv-v1` 的对象，使用字符串字段 `metadata_json` 和 `peaks_csv` 传入两份文件的内容。

所有下列路径以 `/chromaledger/api/` 为前缀，参数 `<id>` 为批次 ID，`<injection>` 为进样 ID。

| 方法与路径 | 行为 |
| --- | --- |
| `GET health`、`GET example` | 检查 SQLite 是否就绪，或获取完整演示输入 |
| `POST preview` | 接收输入包，返回预览结果 |
| `GET runs?offset=0`、`POST runs` | 分页读取目录，或核算并保存输入包 |
| `GET runs/<id>`、`GET runs/<id>/source` | 读取报告或规范化输入 |
| `GET runs/<id>/injections/<injection>` | 查看单次进样证据 |
| `POST runs/<id>/snapshot` | 提交 `{}` 封存可交付组分 |
| `GET runs/<id>/snapshot.json`、`GET runs/<id>/snapshot.csv` | 下载封存件 |
| `GET runs/<id>/snapshot/replay` | 以保存的输入重算封存摘要 |
| `GET runs/<id>/compare?against=<id>` | 返回两批次的样品组分差异 |

### 工作站归档 API（前缀同为 `/chromaledger/api/`，全部只读）

| 方法与路径 | 行为 |
| --- | --- |
| `GET archive/stations` | 列出有收档记录的工作站及批次、封存计数 |
| `GET archive/stations/<station>` | 单个工作站的收件汇总 |
| `GET archive/stations/<station>/receipts` | 该站每份备份的收件记录与批次清单 |
| `GET archive/runs?station=<station>&offset=0` | 归档目录，可按工作站过滤并分页 |
| `GET archive/stations/<station>/runs/<id>` | 读取该站该批次的报告 |
| `GET archive/stations/<station>/runs/<id>/source` | 打开该站原输入 |
| `GET archive/stations/<station>/runs/<id>/injections/<injection>` | 查看单次进样证据 |
| `GET archive/stations/<station>/runs/<id>/snapshot.json`、`snapshot.csv` | 下载原样封存件 |
| `GET archive/stations/<station>/runs/<id>/snapshot/replay` | 以保存的原输入复算封存摘要 |

归档收件使用命令行：`python3 -m tools.intake_archive <station_id> <备份.sqlite3> [--archive-db PATH]`，工作站标识在命令行确认。归档库核验为 `python3 -m tools.check_store <archive.sqlite3> --archive`。

`.project-runtime.json` 指定语法检查和各工作流的独立验证入口。验证使用合成数据与本机临时数据库。系统化测试套件留待后续补充。
