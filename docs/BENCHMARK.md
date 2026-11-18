# SQLite vs PostgreSQL 实测对比

> 2026-11-14 实测。结论：**个人单机使用继续用 SQLite**。
> 测试用的 PG schema 已删除，未在你库里留下任何数据。

## 1. 测试环境

| 项 | 配置 |
|---|---|
| 机器 | 同一台 Windows 机器 |
| SQLite | Python 3.12 内置，`mmap_size=256MB`、`cache_size=64MB`、WAL |
| PostgreSQL | 18.6（Docker 容器 `lh_pg`，18 核可见），默认配置 `shared_buffers=128MB`、`work_mem=4MB`、并行 2 |
| 数据 | 同一份：`valuation_daily` 622 万行 + `bars` 710 万行 |

## 2. 方法

1. SQLite 导出 CSV（287 MB + 353 MB）
2. `docker cp` 进容器 → `\copy` 入 PG（366 秒）
3. 建同样的主键 `(symbol, date)` 和 `date` 索引，**`CLUSTER` 物理排序**（对齐 SQLite 的聚簇主键）+ `ANALYZE`（401 秒）
4. 跑同一组 SQL，`\timing` 计时

## 3. 结果

| 操作 | SQLite | PG 默认 | PG 调优后* |
|---|---|---|---|
| 点查 K 线 160 根 | **0.10 ms** | 0.33 ms | 0.33 ms |
| 622 万行 `SUM/COUNT` | **613 ms** | 1500 ms | 1285 ms |
| 622 万行 `GROUP BY symbol` | **788 ms** | 1431 ms | 1281 ms |
| 窗口函数算 PE 分位 | 5442 ms | 16676 ms | **1248 ms** |
| `UPSERT` 5500 行 | **18.5 ms** | 29 ms | — |
| 两表占用空间 | 947 MB | 1673 MB | — |

\* 调优 = `work_mem=256MB` + `max_parallel_workers_per_gather=4` + `jit=off`

## 4. 三个结论

**① PG 的性能强依赖配置。**
窗口函数 16.7 秒不是引擎慢，是 `work_mem=4MB` 装不下 5448 个分组，落盘排序。
提到 256MB 后 1.25 秒，快 13 倍并反超 SQLite。**PG 上限高，下限也低。**

**② 数据量不大时，存储密度比并行度更重要。**
6.2M 行扫描 SQLite 反而快一倍（613ms vs 1285ms）。PG 已用并行 seq scan，但
每行有元组头 + 文本指针 + 对齐填充，同样数据占 719MB，SQLite 只占 314MB。
扫的字节数少，就赢。

**③ 点查 SQLite 快 3 倍。**
0.10ms vs 0.33ms：PG 每次要过 TCP、解析、规划、执行；SQLite 是进程内读内存映射页。
单用户一屏 50 只票的详情，这个差距会累积。

## 5. 决策

个人单机使用，继续 SQLite：

| 维度 | 胜者 |
|---|---|
| 点查延迟 | SQLite（3×） |
| 简单聚合 | SQLite（2×） |
| 复杂窗口分析 | PG 调优后（4×） |
| 小批量写入 | SQLite（1.6×） |
| 存储体积 | SQLite（1.8×） |
| 并发多用户 | PG |
| 运维成本 | SQLite（零） |

更关键：**应用层 2 秒的瓶颈在 pandas，不在数据库**。换 PG 顶多把 SQL 从 0.3s 降到 0.2s，
用户无感知。

## 6. 什么时候再考虑迁移

只有 **多人同时使用** 这一条。SQLite 单写者，两人同时点「同步」会互相阻塞；
PG 的 MVCC 才解决这个问题。届时顺带把分位预计算改成物化视图 + 定时刷新。

迁移工作量估计 1~2 天：schema 基本能直译，主要是
`COPY` 批量导入、`ON CONFLICT`（已有）、以及用 `CLUSTER` 替代 `WITHOUT ROWID`。

## 7. 复现步骤

```bash
# 1. SQLite 导出
python -c "..."          # 见 _export.py 的逻辑，导出 valuation_daily / bars 为 CSV

# 2. 建库导数据
docker exec lh_pg psql -U lhroot -d adata -c "CREATE SCHEMA bench; ..."
docker cp data/bench_valuation.csv lh_pg:/tmp/
docker exec lh_pg psql -U lhroot -d adata -c "\copy bench.valuation_daily FROM '/tmp/bench_valuation.csv' WITH (FORMAT csv)"

# 3. 聚簇 + 统计
docker exec lh_pg psql -U lhroot -d adata -c "CLUSTER bench.valuation_daily USING valuation_daily_pkey;" -c "ANALYZE bench.valuation_daily;"

# 4. 计时
docker exec lh_pg psql -U lhroot -d adata -c "\timing on" -c "SELECT count(*), avg(pe_ttm) FROM bench.valuation_daily;"
```
