# plugin.video.douban

Kodi 豆瓣推荐插件 — 基于豆瓣 API 获取电影、电视剧、综艺推荐列表，支持多维度筛选、TMDb 元数据补充、自动加载翻页。

## 功能

- **热门推荐**：电影/电视剧/综艺的热门、最新、经典榜单
- **多维度筛选**：分类 → 类型 → 地区 → 年代 → 排序，5 级联动筛选
- **FilterBrowse 视图适配**：实现 `filter_categories/filter_genres/filter_regions/filter_years/filter_sorts` 路由契约，适配 `skin.arctic.fuse.3` 的 View 543 多行筛选条视图
- **TMDb 元数据补充**：后台线程并行搜索 TMDb，替换防盗链海报、补充剧情/演员/导演/评分等
- **双层缓存**：
  - 列表缓存：文件系统 JSON + TTL（30 分钟），page=1 优先读缓存秒加载
  - TMDb 缓存：内存（1h）→ 磁盘（7 天），重启后无需重新搜索
- **自动加载翻页**：插件端触发器 + 皮肤端 `Container.Update` 协同，无限滚动
- **季/集导航**：TMDb 季列表 → 集列表，支持剧集逐集浏览
- **播放集成**：电影直接调用盘搜播放，剧集支持自动播放
- **豆瓣图片反代**：Nginx 反代解决 `img.doubanio.com` 418 防盗链

## 安装

1. 下载本仓库到 Kodi addons 目录：
   ```
   %APPDATA%\Kodi\addons\plugin.video.douban\
   ```
2. 重启 Kodi

### 依赖

- Kodi 19+ (Python 3 / xbmc.python 3.0.0)
- `script.module.requests` ≥ 2.22.0
- 可选：`plugin.video.pansou`（播放搜索）

## 设置

| 设置项 | 说明 | 默认值 |
|--------|------|--------|
| TMDb API Key | themoviedb.org API 密钥 | 内置公共 Key |
| TMDb 语言 | 搜索/详情语言 | zh-CN |
| Enrich 并发数 | 后台 TMDb 搜索线程数 | 4 |
| 豆瓣图片反代地址 | Nginx 反代 URL | http://192.168.99.184:8443 |

## 项目结构

```
plugin.video.douban/
├── addon.py                    # 主入口：路由分发 + 缓存 + enrich + 列表渲染
├── addon.xml
├── resources/
│   ├── settings.xml
│   ├── language/
│   │   └── resource.language.zh_cn/strings.po
│   └── lib/
│       ├── doubanApi.py        # 豆瓣 API：推荐列表 + 筛选数据
│       ├── tmdbApi.py          # TMDb API：搜索 + 详情 + 季/集 + 双层缓存
│       ├── httpClient.py       # requests 封装
│       ├── settings.py         # 设置管理 + 内存缓存
│       └── logger.py           # 日志
```

## 缓存架构

```
列表请求 (page=1)
  ├─ 读文件缓存(TTL=30min) → 命中 → 直接渲染，跳过 API + enrich
  └─ 未命中 → 豆瓣 API → 渲染 → 后台 enrich → 写回缓存

TMDb 搜索
  ├─ 内存缓存(1h) → 命中 → 返回
  ├─ 磁盘缓存(7天) → 命中 → 回填内存 → 返回
  └─ 未命中 → TMDb API → 写内存 + 磁盘

自动加载翻页 (page>1)
  → 读文件缓存 → 追加新页数据 → 去重 → 写回缓存 → 渲染
```

缓存文件位于 `%APPDATA%\Kodi\userdata\addon_data\plugin.video.douban\`：
- `cache_*.json` — 列表/单项数据
- `tmdb_*.json` — TMDb 搜索结果

## FilterBrowse 视图契约

适配 `skin.arctic.fuse.3` View 543 的插件端路由契约：

| 路由 | 说明 | 关键属性 |
|------|------|----------|
| `filter_categories` | 分类维度（电影/电视剧/综艺） | `filter_value` |
| `filter_genres` | 类型维度（按分类动态返回） | `filter_value`, 接受 `cat_id` |
| `filter_regions` | 地区维度（按分类动态返回） | `filter_value`, 接受 `cat_id` |
| `filter_years` | 年代维度 | `filter_value` |
| `filter_sorts` | 排序维度（热度/评分/时间） | `filter_value` |
| `filter_list` | 筛选结果内容 | 接受全部筛选参数 |

每个路由返回的 ListItem 必须设置 `filter_value` 属性，皮肤端通过 `Container(id).ListItem.Property(filter_value)` 读取选中值。

## 豆瓣图片反代

豆瓣图片 `img*.doubanio.com` 需要正确的 Referer，Kodi 图片加载器不会携带。部署 Nginx 反代：

```nginx
server {
    listen 8443;
    location ~ ^/img(\d+)/(.*)$ {
        resolver 192.168.99.1;
        proxy_pass https://img$1.doubanio.com/$2;
        proxy_set_header Referer https://movie.douban.com/;
        proxy_set_header Host img$1.doubanio.com;
        proxy_ssl_server_name on;
    }
}
```

## License

MIT