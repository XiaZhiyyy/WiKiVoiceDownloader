# WikiVoiceDownloader

一个命令行工具，用于从 Wiki 站点（碧蓝航线 Biligame Wiki / Koumakan Wiki）的角色语音页面批量下载语音音频，并生成配套的台词文本记录。严格串行请求、可断点恢复、编号永久稳定，适合游戏语音素材的整理与采集。

## ✨ 功能特性

- **多站点支持**：Biligame 碧蓝航线 Wiki、Koumakan Wiki（英文 / 日文 / 中文文本分区）
- **交互式选择**：自动解析语音分组（皮肤 / 事件），支持数字多选（`1,3`）或全选（`A`）
- **稳定编号**：音频 ID 一经分配永久不变，跨视图、跨语言复用同一组媒体文件
- **断点恢复**：中断或失败后可继续补全，已有效的文件不会重复下载
- **完整性校验**：MP3 帧结构检查；Ogg（Vorbis / Opus）页结构、CRC、必需头部与 EOS 校验
- **安全停止**：遇到验证码或访问限制时自动停止并保留已完成内容，不盲目重试
- **本地导入**：可在正常浏览器访问后保存页面 HTML，离线导入解析
- **合规设计**：不绕过验证码、不切换 IP、不批量遍历全站、不下载登录态内容

## 📦 环境要求

- Python 3.11 或更新版本
- Windows / Linux / macOS
- 可访问目标 Wiki 站点的网络

## 🔧 安装

**Windows**：首次双击 `setup.bat` 安装依赖，之后双击 `start.bat` 启动（不自动升级依赖，不覆盖已有 `config.json`）。

**手动安装**：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 🚀 快速开始

Biligame（无需选择服务器分区）：

```powershell
.\.venv\Scripts\python.exe main.py "https://wiki.biligame.com/blhx/信浓"
```

Koumakan（选择日文分区）：

```powershell
.\.venv\Scripts\python.exe main.py "https://azurlane.koumakan.jp/wiki/Shinano/Quotes" --server jp
```

只读预览（不下载、不写任何文件）：

```powershell
.\.venv\Scripts\python.exe main.py "https://azurlane.koumakan.jp/wiki/Shinano/Quotes" --server jp --dry-run
```

启动后程序会列出语音分组，选择要下载的分组即可。也可以直接运行 `main.py` 按提示输入 URL。数字多选支持 `1,3`、`3,1`、`1,1,3`，实际编号按页面 DOM 顺序；`A` 表示当前分区全部分组。

分区优先级：`--server` > URL 锚点 > 交互选择。目标分区不存在或未加载会停止，不回退英文。**Server 是网页的文本分区，不是声音语言鉴定。**

## ⌨️ 命令行参数

| 参数 | 说明 |
|---|---|
| `url` | 角色 Wiki 页面 URL（位置参数） |
| `--server jp\|cn\|en` | 选择文本分区；优先级高于 URL 锚点 |
| `--name` | 覆盖显示名称（目录名） |
| `--parser` | 手动指定解析器（默认自动识别） |
| `--config` | 指定配置文件路径 |
| `--html-file PATH` | 使用本地 HTML/TXT 文件代替网络请求 |
| `--html-encoding ENC` | 本地文件的文本编码（默认严格 UTF-8/BOM） |
| `--dry-run` | 只读分析，不分配编号、不产生输出 |
| `--version` | 显示版本号 |

## 📁 输出结构

```text
downloads/
  Shinano/
    audio/
      001.ogg
      002.mp3
    Shinano.txt        # 四字段台词记录
    metadata.json      # 恢复与复用依据
    .wvd_backups/      # 视图切换时自动备份
```

- 同一 URL 的音频是媒体主键：跨视图 / 跨语言选择不会重复下载
- 失败的编号保留，新条目在最大已分配编号后追加，跳号属正常现象
- 超过 999 自然进位为 `1000`，请使用数字排序查看
- 不同 URL 即使字节相同也不合并；不同站点的同名角色不自动合并
- 未选择的内容不会被删除，也不会同步 Wiki 端的删除事件

## 📝 TXT 四字段格式

UTF-8、无 BOM、LF 行尾；每行一个音频，按 ID 排序：

```text
001.ogg|Default Skin|Self Introduction|台词正文
002.ogg|Default Skin|Login|[无台词]
```

- 字段内的竖线写作 `\|`，反斜杠写作 `\\`；请勿使用简单的 `split('|')` 解析
- 分组名和事件名保留网页原文，不自动翻译
- 目标语言缺失或配对不可靠时仍保存有效音频，正文记为 `[无台词]`；不借用其他分区、相邻行或英文译文填补
- 正确的解析方式：

```python
from wiki_voice_downloader.text_format import parse_record
from pathlib import Path
for line in Path('Shinano.txt').read_text(encoding='utf-8').splitlines():
    filename, group, event, body = parse_record(line)
```

> 此格式不是模型的直接训练清单；训练前需要独立核对台词、发音和授权。

## ⚙️ 配置

默认无需配置即可运行。需要自定义时，复制 `config.example.json` 为 `config.json`。常用项：

```json
{
  "download_dir": "downloads",
  "log_dir": "logs",
  "max_attempts": 3,
  "proxy": null
}
```

- 默认总尝试次数 3（含首次）；严格串行，请求间不主动等待，只对错误退避或服务器 `Retry-After` 等待
- 不绕过验证码、不切换 IP、不关闭 TLS、不默认继承环境代理

## 🔧 自定义站点规则

当目标站点改版导致解析失败时，可基于内置规则创建本地适配文件：

```json
{"site_profile_dir": "my_site_profiles"}
```

复制 `wiki_voice_downloader/site_profiles/koumakan_azurlane.json` 到该目录后修改分区 / 分组 / 表格 / 播放入口 / 语言节点选择器、列位置、表头、排除节点及已确认资源 host/path。规则只在下次启动时本地加载，无自动更新、无网络插件。不能承诺任意网页改两个选择器就能正确配对，修改后建议运行对应回归测试：

```powershell
python -m pytest -q tests/test_koumakan.py::test_wrong_columns_rejected_and_profile_only_change_handles_layout
```

## 📥 人工 HTML 导入

普通 HTTP 遇到明确或疑似验证时，程序会停止自动请求，由用户选择导入本地文件、打开普通浏览器或退出。也可以不经网页请求，直接使用已正常访问后保存的页面：

```powershell
# 只读预览，零网络、不修改任何输出
.\.venv\Scripts\python.exe main.py "https://azurlane.koumakan.jp/wiki/Shinano/Quotes" --server jp --html-file "D:\Wiki素材\quotes.txt" --dry-run

# 正式下载（选择分组后下载公开音频）
.\.venv\Scripts\python.exe main.py "https://azurlane.koumakan.jp/wiki/Shinano/Quotes" --server jp --html-file "D:\Wiki素材\quotes.txt"
```

- 支持 `.html`、`.htm`、包含原始 HTML 的 `.txt`；默认严格 UTF-8/BOM
- 仅当已知原文件编码时才显式指定 `--html-encoding shift_jis` 等
- 不支持 MHTML、PDF、图片或 ZIP 改名伪装
- 页面导入成功不代表音频 CDN 可访问，这不是"完全离线下载"
- 不读 Cookie、不连接调试端口、不操作验证控件

## ❓ 常见错误

| 错误 | 含义与处理 |
|---|---|
| `unsupported_layout` | 页面结构与预期不符；保存样本核实，不要猜测 API 或捆绑浏览器绕过 |
| `missing_section` | 所选分区不存在、未加载或无受支持语音行；不要拿其他分区当替代 |
| `missing_target_language` / `ambiguous_pairing` | 音频可保存但台词不可靠；不会用其他语言自动填补 |
| `unsupported_media_format` / `invalid_media` | 超出支持的格式子集，或数据完整性不合格；文件后缀和 MIME 不是判断依据 |
| `storage_conflict` | metadata 冲突或损坏；保留现场停止，恢复备份或换目录，不会重置编号 |

**退出码**：`0` 成功或正常取消；`1` 致命错误（配置 / 解析 / 网络 / 存储或站点停止）；`2` 本轮存在单项失败；`130` 用户中断（Ctrl+C）。

## ⚠️ 已知限制

- 遇到验证码时程序会停止；可在正常浏览器中访问后保存 HTML，用 `--html-file` 导入解析（音频仍需网络下载）
- 媒体校验是结构与传输层面的检查，不是完整解码或可播放保证；不转码、不重采样、不改标签、不裁剪
- 切换文本视图时，先备份原 TXT 再激活新视图；备份延后到至少一条目标记录可用时才发生

## 🧪 开发与测试

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

默认测试拦截外网请求，使用临时目录与本地 HTTP，不会修改你的下载数据。对自存的网页 HTML 做只读解析检查：

```powershell
python tools/verify_user_fixture.py "保存的页面.html"
```

显式在线冒烟检查（独立于默认测试，最多临时下载 3 个音频，不写入下载目录）：

```powershell
python tools/smoke_test.py "https://azurlane.koumakan.jp/wiki/Shinano/Quotes" --server jp --audio-limit 1 --report smoke-jp.json
```

## 📦 构建 Windows 可执行程序

在 Windows 上运行 `build_exe.bat`（控制台 onedir 打包）。分发时必须包含整个 `dist/WikiVoiceDownloader/` 目录，不能只复制其中一个 EXE。

## 📄 授权

本项目代码基于 [MIT License](LICENSE) 发布。本授权不涵盖第三方 Wiki 文本、录音、游戏素材及依赖，各自权利由其相应授权决定。只保存你有权使用的内容，并遵守站点的正常访问要求；程序能够下载不代表素材可以任意再分发或训练。
