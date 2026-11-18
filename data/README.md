# data/

数据库与缓存目录，**不纳入版本控制**（见根目录 `.gitignore`）。

| 文件 | 说明 |
|---|---|
| `astock.db` | 主库：日线、财务、股东户数、估值、形态信号、用户与收藏（约 1.1 GB） |
| `astock.db-wal` / `-shm` | SQLite WAL 运行文件，随库自动产生 |
| `pinyin_src.txt` | 拼音字库源文件（已纳入 git，`build_pinyin.py` 用它生成 `stock_pinyin` 表） |

## 说明

- 新环境首次启动会自动建空库（表结构 + 12 个默认筛选方案）；
  需要行情时跑 `python build.py init`（全量，20~40 分钟）或 `python build.py update`（增量）。
- **`astock.db` 里有用户密码哈希和会话 token，不要提交、不要外传**。
- 因为库不在 git 里，请单独做备份（NAS 快照 / `rsync` / `sqlite3 .backup`）。
- Docker 部署时这个目录挂载到容器的 `/app/data`，必须是本地存储，不要挂 SMB/NFS。
- 这个目录默认被同步客户端（OneDrive / 坚果云 / fnOS sync）盯着的话，SQLite 的 `-shm` 会被占用，
  容器里会报 `disk I/O error`；建议把库挪到本地目录，用 `ASTOCK_DB` / `ASTOCK_DATA_DIR` 指过去。
