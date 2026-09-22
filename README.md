# WikiVoiceDownloader 1.0.0

单角色 Wiki 语音下载器。本次在已交付的 v1.0.0 源码上增量修改，没有回退到 v1.0。应用版本继续为 1.1.0，R2 是本次修订标记。

## 本次交付与验证边界

这是**可运行的源码项目**，不是已构建的 Windows EXE。

- 新增 HTTP / 本地 HTML 统一页面来源、人工导入、访问响应分类及空视图保护。保留 Biligame、Koumakan、日文单语正文、MP3/Ogg、稳定 ID 和 schema 1 迁移。
- 本次已收到并核验用户 HTML：3 个 Server，每区 96 个唯一音频；日文 7 组、29 条附带英文、66 条隐藏音频行。数量只绑定该样本，不是线上固定阈值。
- 当前容器对网页与示例音频的普通请求仍为 ConnectionError。没有取得现场验证页，具体验证供应商仍未确认。分类和重试测试使用合成响应，不代表已自动通过网站验证。
- Windows 真人浏览器步骤、BAT、EXE 构建与运行未现场验证。实际测试和限制见 [测试报告](docs/test_report.md)。

## 安装与启动

需要 Python 3.11 或更新版本；本轮实际测试为 Linux / Python 3.13.5。解压到持久、可写目录。

Windows 首次双击 `setup.bat` 安装依赖，之后双击 `start.bat`。启动不会自动升级依赖，不覆盖已有 `config.json`。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

Biligame 保留原有流程，不额外增加语言问答：

```powershell
.\.venv\Scripts\python.exe main.py "https://wiki.biligame.com/blhx/信浓"
```

Koumakan 新流程（在线可用性仍需在正常可访问的网络验收）：

```powershell
.\.venv\Scripts\python.exe main.py "https://azurlane.koumakan.jp/wiki/Shinano/Quotes#tabber-Japanese_Server"
.\.venv\Scripts\python.exe main.py "https://azurlane.koumakan.jp/wiki/Shinano/Quotes" --server jp --dry-run
```

保留 `url`、`--config`、`--name`、`--parser`、`--dry-run`、`--version`，支持 `--server jp|cn|en`，R2 新增 `--html-file` 与 `--html-encoding`。先选一个服务器文本分区，再选其中的分组；`A` 只表示当前分区全部分组。数字多选支持 `1,3`、`3,1`、`1,1,3`，实际编号仍按 DOM 顺序。

分区优先级：`--server` > 已识别且实际存在的 URL 锚点 > 交互单选。锚点不发送给 HTTP。目标分区不存在或未加载会停止，不回退英文；网页的 ARIA 选中状态不能覆盖用户选择。**Server 是网页的文本分区，不是声音语言鉴定。**

## 人工 HTML 导入（R2）

普通 HTTP 遇到明确或高置信疑似验证时，程序先停止自动请求，再由用户选择导入、打开普通浏览器或退出。不读 Cookie、不连接调试端口、不操作验证控件。普通登录要求或访问拒绝不被当作验证菜单。

可以不经过网页请求，直接使用已正常访问后保存的正文：

```powershell
.\.venv\Scripts\python.exe main.py "https://azurlane.koumakan.jp/wiki/Shinano/Quotes" --server jp --html-file "D:\Wiki素材\quotes.txt" --dry-run
.\.venv\Scripts\python.exe main.py "https://azurlane.koumakan.jp/wiki/Shinano/Quotes" --server jp --html-file "D:\Wiki素材\quotes.txt"
```

第一条是零网络、零输出修改的预览；第二条选择分组后会**正式下载公开音频**。页面导入成功不代表音频 CDN 可访问，所以这不是“完全离线下载”。

支持 `.html`、`.htm`、包含原始 HTML 的 `.txt`；默认严格 UTF-8/BOM。仅当已知原文件编码时，可显式加 `--html-encoding shift_jis` 等有效文本编码。不支持 MHTML、PDF、图片或 ZIP 改名伪装成 HTML。

非交互 dry-run 在已明确 Server 时预览该分区的全部可见组；正式下载仍需按原流程选择。缺必要参数时不会永久等待输入。

音频遇验证/拒绝后停止后续请求，已完成项保留，未执行项保留原 ID。新目标视图至少有一条有效或已复用的音频后，才备份并切换主 TXT。没有可用日文目标媒体时，不用空 TXT 覆盖英文历史。

完整 Windows 操作步骤见 [人工保存与导入](docs/manual_html_import.md)；分类证据与边界见 [访问处理](docs/access_handling.md)。

## 日文 TXT 与媒体复用

Japanese Server 默认只把可靠的 `ja` 正文写入第四字段，不添加 `[ja]`，也不拼接英文译文。实际取得的译文仍按其原 Server 引用保存在 metadata。数字、`Zzzz`、括号、外语片段和原标点不会被字符过滤器删掉。

```text
001.ogg|Default Skin|Self Introduction|网页中同一条目的可靠日文正文
002.ogg|Default Skin|Login|[无台词]
```

以上仅为格式示意，不是实际下载记录。分组名和事件名保留网页原文，不自动翻译。目标语言缺失或配对不可靠仍保存有效音频，正文为 `[无台词]`；不借用其他 Server、相邻行、英文译文或 ASR 填空。

同一站点同页的 English / Japanese / Chinese 共用一组媒体 ID 和 `audio/`。English 全选后切 Japanese 继续/补全时，已有效媒体无需再次请求；新增日文引用并重建当前 TXT。**本轮已用用户真实 HTML 与受控媒体响应验证零额外音频请求；不是线上音频验收。**

一个主 TXT 表示一个明确的当前视图：日文分区第一次选原皮、第二次选皮肤会累积该日文视图的已选有效条目；英文历史中选择过、但日文未选择的皮肤不会自动混入。其他视图的选择、引用和音频不删除。

切换视图的备份延后到至少一条目标记录可用时，且先备份原 TXT 再激活新视图。已有手工修改也能进入切换备份。取消、无效输入和 dry-run 不备份、不迁移、不分配编号。

## 稳定编号、原始格式与恢复

```text
downloads/
  Shinano/
    audio/
      001.mp3
      002.ogg
      003.ogg
    Shinano.txt
    metadata.json
    .wvd_backups/   # 仅需要迁移/切换备份时出现
```

同一数据集规范化来源 URL 是媒体主键，路径大小写和有效 query 保留，fragment 去除。不同 URL 即使字节相同也不按内容哈希合并；Biligame 与 Koumakan 同名角色不自动合并。

所有新 ID 在首个音频请求前持久化；失败号保留，新项在最大已分配号后追加。TXT 只列出已提交且本地校验有效的文件，允许暂时跳号。不按皮肤或格式重新从 001 开始。超过 999 自然使用 `1000`；应使用数字/自然排序，不依赖字符串排序。

媒体以内容检测为准：MP3 沿用既有帧边界检查；Ogg 支持单逻辑流、非链式 Vorbis，以及 Opus mapping family 0 的单声道/双声道。Ogg 检查页边界、序号、CRC、跨页包、必需头部和完整 EOS；Vorbis 另检查 setup 配置结构，Opus 检查包尺寸。未知编码、视频、链式/复用 Ogg、其他 Opus mapping 会明确报告不支持。单包 16 MiB、Vorbis 单码本 262144 项是校验资源预算，不是短音频过滤。

这些是**结构与传输校验，不是完整解码或可播放保证**。不转码、不重采样、不改标签、不降噪、不裁剪，不按最小时长/体积过滤。20 毫秒的真实编码数学正弦测试样本可以通过；不包含下载的整套游戏语音。

未知首次格式使用 `001.media.part`；有提示时可用 `001.ogg.part` / `001.mp3.part`。首次成功后按检测格式提交 `.mp3` 或 `.ogg`，编号不变。已完成的同 URL 若覆盖时改了格式，报告 `source_format_changed`，不偷偷改旧文件名或破坏旧有效版本。

继续/补全复用有效文件并修复本次所选缺失项；覆盖只重下选中的项，成功才替换其字节与引用版本。普通继续可补新引用、新语言和空正文；同一引用、同一语言的远端正文改变会诊断并保留旧值。未选择内容不删除，不同步 Wiki 删除事件。

metadata 是恢复依据；TXT 是可以重建的派生文件。JSON、音频和 TXT 分别提交，不宣称它们是一个跨文件原子事务。提交日志记录暂存路径和新哈希，中断后在用户确认继续时恢复。一个进程内对未变化文件缓存已通过的校验结果；下一次运行重新检查，stat/哈希身份改变也会重新检查。


## 四字段与转义

UTF-8、无 BOM、LF；无标题行，一文件最多一行，按持久整数 ID 排序。音频路径解释为 `TXT 所在目录 / audio / 第一字段`。

四个字段中的反斜杠写为 `\\`，竖线写为 `\|`，布局换行归一化为空格。不要用普通 `split('|')`。

```python
from wiki_voice_downloader.text_format import parse_record
from pathlib import Path
for line in Path('Shinano.txt').read_text(encoding='utf-8').splitlines():
    filename, group, event, body = parse_record(line)
```

Biligame legacy 视图保留 v1.0 已有的多语言渲染约定。网页只提供翻译时，不能由此推断音频实际说出的字词。本格式不是 GPT-SoVITS 等模型的直接训练清单；训练前需要独立核对台词、发音和授权。

## 配置与本地站点规则

默认无配置可运行；需要时复制 `config.example.json` 为 `config.json`。旧配置不包含新字段也可以使用。默认总尝试次数 3，包含首次；严格串行，正常成功请求之间不主动 sleep。只对错误退避或服务器 Retry-After 等待；等待超过上限或访问受限时停止。不绕过验证码、不切换 IP、不关闭 TLS，不默认继承环境代理。

相对输出路径默认基于应用目录；显式 `--config` 时其中的相对输出路径基于配置文件目录。新增的 `site_profile_dir` 相对路径始终基于应用目录，和运行时 cwd 无关。代理只读取明确配置，默认 `null`。

```json
{"site_profile_dir": "my_site_profiles"}
```

复制 `wiki_voice_downloader/site_profiles/koumakan_azurlane.json` 到该目录再修改。可改分区/分组/表格/播放入口/语言节点选择器、列位置、表头、排除节点及已确认资源 host/path。规则只在下次启动本地加载；没有自动更新、任意 Python 导入或网络插件。

只改按钮类名和列顺序的实际回归：

```text
python -m pytest -q tests/test_koumakan.py::test_wrong_columns_rejected_and_profile_only_change_handles_layout
```

参见 [规则 schema](docs/site_profile_schema.md) 与 [适配器维护](docs/parser_development.md)。不能承诺任意网页改两个选择器就能正确配对。

## 验证与 Windows 构建

```text
python -m pip install -r requirements-dev.txt
python -m pytest -q
python tools/verify_manifest.py
```

默认测试拦截外网，使用临时目录、Fake Session 或本地 HTTP，不修改用户下载数据。对用户原始 HTML 运行只读检查：

```text
python tools/verify_user_fixture.py "日wiki网页布局.txt"
```

显式在线检查独立于默认测试，最多临时下载 3 个音频；不会保存到用户下载目录。报告和 HTML 使用排他创建，不覆盖已有文件：

```text
python tools/smoke_test.py "https://azurlane.koumakan.jp/wiki/Shinano/Quotes" --server jp --audio-limit 1 --report smoke-jp.json
```

在 Windows 运行 `build_exe.bat`，使用控制台 onedir 打包。必须分发整个 `dist/WikiVoiceDownloader/`，不是只复制其中一个 EXE。`.spec` 已明确收入内置 JSON；外部可编辑目录与内置资源分开。当前仅交付构建能力，具体未验证清单见 [Windows 验收](docs/windows_acceptance.md)。

退出码：`0` 成功或普通取消；`1` 致命配置/解析/网络/存储错误或站点停止；`2` 本轮有单项失败；`130` Ctrl+C。无语音结构或目标分区缺失不会伪装为“成功下载 0 条”。

## 常见问题

`unsupported_layout`：普通 HTTP 返回内容可能与浏览器保存 DOM 不同。先核实响应并保存授权样本，不猜 API、不捆绑浏览器绕过。

`missing_section`：所选 Server 不存在、未加载或没有受支持语音行；不要把其他分区当替代。

`missing_target_language` / `ambiguous_pairing`：媒体可以保存，但正文不可靠；检查引用，不用其他语言自动填补。

`unsupported_media_format` / `invalid_media`：分别表示超出支持子集、或数据结构/完整性不合格。来源后缀和 MIME 不是最终依据。

`storage_conflict` / metadata 损坏：保留现场并停止，恢复一致备份或使用不同目录，不重置 ID。路径不可写时配置可写的 download_dir；文件被占用时关闭占用程序再继续。

## 项目结构与授权边界

主要代码位于 `wiki_voice_downloader/`：`page_sources/` 统一 HTTP/本地输入，`access_detection.py` / `manual_import.py` 处理访问分类与人工交接，`parsers/` 处理局部 HTML 配对，`profile_loader.py` / `site_policy.py` 处理可信本地规则和来源，`media/` 处理格式，`views.py` 处理引用与导出视图，`migrations.py` / `storage.py` 处理持久化与恢复，`service.py` / `cli.py` 编排流程。

只保存用户有权使用的内容，并遵守站点正常访问要求。程序能够下载不代表素材可以任意再分发或训练。本项目不翻译、不做 ASR、不批量遍历全站，也不下载登录态内容。