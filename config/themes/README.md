# 主题包（冷启动种子）

本目录下的 YAML **不是**日常运行时业务配置。

## 用途

- 在 **公告/年报自动抽取**（`ExposureEdgeExtractor`）尚未覆盖时，提供少量示例暴露边与实体别名
- 本地开发验证图谱反查与事件 ingest 链路

## 推荐流程

```bash
# 1. 可选：导入示例种子（非必须）
python main.py --import-theme-pack changxin_chain

# 2. 推荐：从自选股公告自动抽取暴露边
python main.py --extract-exposure-edges

# 3. 图谱同步（补全别名、自选股节点）
python main.py --sync-exposure-graph

# 4. 事件 ingest（查询词由图谱推导，无需配置 THEME_NEWS_KEYWORDS）
python main.py --run-exposure-ingest --force-exposure-ingest
```

## 原则

| 应写入图谱 | 不应写入 `.env` |
|------------|-----------------|
| `company_exposure` 边（来源：公告/年报/抽取） | 具体产业链股票列表 |
| `entity_alias` 别名 | `THEME_NEWS_KEYWORDS` 枚举业务主题 |
| `company_profile` 表观主业 | 长鑫/果链等举例当作运行时配置 |

`changxin_chain.yaml` 仅为**举例**（合肥城建 ↔ 长鑫），生产环境应优先信任 `source=announcement` 的抽取边。
