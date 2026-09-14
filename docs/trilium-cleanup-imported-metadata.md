# 清理 Trilium 导入笔记顶部自动生成的元数据

本目录的脚本用于清理从思源笔记导入到 [Trilium](https://github.com/zadam/trilium)
（或 TriliumNext）时，每篇文档顶部自动生成的一段迁移元数据。

仓库内的清理脚本是：

```text
scripts/trilium-cleanup-imported-metadata.js
```

英文版本可使用 `README_EN.md` 中的对应章节。

## 背景

通过 `siyuan-to-trilium` 导出的 Markdown ZIP，在思源笔记中使用 YAML front matter
保存 `title` / `date` / `lastmod`。Trilium 在导入时把整段 front matter 解析成
HTML 后，会被包在文档顶部第一个 `<h1>` ~ `<h6>` 里：

```html
<h2>title: flag
date: 2023-02-28T12:50:55+08:00
lastmod: 2025-02-07T22:51:42+08:00</h2>
<p></p>
```

这些元数据既与 Trilium 自身的属性（`dateCreated`、`dateModified` 等）重复，
又会污染文档正文。本仓库的导出器有意不再处理，避免重建整篇 HTML 引入回归。
清理工作交由本脚本，在导入完成后一次性处理。

## 工作原理

脚本是一个 Trilium **JS Backend** 代码笔记，运行后会：

1. 遍历 `root` 的所有子笔记（递归处理整棵笔记树），按 `noteId` 去重。
2. 只处理类型为 `text` 的笔记；其他类型（Code、File、Image 等）跳过并计入 `skipped`。
3. 读取每篇笔记的 HTML 内容，检测顶部第一个 `<h1>` ~ `<h6>` 是否**同时**包含
   `title:`、`date:`、`lastmod:` 三个字段。
4. 命中时删除该标题节点，并继续清理紧随其后的空白 `<p>` 与 `<hr>`。
5. 把处理后的内容通过 `note.setContent()` 写回。

脚本不会重建文档正文，也不会触碰正文里偶然出现的 `title:` / `date:` /
`lastmod` 字面量。

> 清理规则必须**三者同时**出现在同一标题节点内才会触发，避免误删正常正文。

## 前置条件

- 已经使用本仓库的导出器生成 `siyuan-archive.zip`，并通过
  `Import into note → Markdown (ZIP)` 完成导入。
- Trilium 实例能够创建 JS Backend 代码笔记（默认安装即支持）。

## 备份建议

脚本只会修改 `text` 类型笔记的内容。为降低风险，建议在执行前：

- 在 Trilium 顶部菜单中选择 `Tools → Database Backup` 备份一次数据库。
- 或者在目标笔记上启用版本控制（`Note context menu → Note Revisions`），
  便于事后回滚。

## 使用步骤

1. 在 Trilium 笔记树中右键任意笔记，选择
   `Advanced → Code notes` 或 `Note navigation → Open JS backend`，
   新建一个名为 `Cleanup Imported Metadata` 的笔记。
2. 将代码笔记的编程语言切换为 `JavaScript (backend)`。
3. 打开 `scripts/trilium-cleanup-imported-metadata.js`，把全文复制粘贴进去并保存。
4. 在代码笔记的工具栏上点击 `Run`（运行图标）执行。

执行后会在脚本顶部和 Trilium 日志面板输出统计：

```text
======================================
开始全库清理
======================================
======================================
全库清理完成
扫描：2547
匹配：xxx
清理：xxx
跳过：395
失败：0
======================================
```

字段含义：

- `扫描`：实际处理的 Text Note 数量（按 `noteId` 去重）。
- `匹配`：检测到顶部元数据的笔记数；**不应为 0**，否则需要重新检查导入是否完成。
- `清理`：实际调用 `setContent` 修改内容的笔记数。
- `跳过`：非 text 类型、或内容为空的笔记数。
- `失败`：处理过程中抛错的笔记数，正常情况下应为 0。

## 验证清理结果

1. 在 Trilium 笔记树中随机打开几篇之前确认存在元数据的笔记，
   确认顶部不再有标题块。
2. 如果脚本中 `匹配` 为 0，说明导入产物里没有该结构，请不要反复执行；
   可以先在任意一篇疑似笔记上执行只读诊断：

   ```javascript
   const note = api.getNote("<noteId>");
   api.log(note.getContent());
   ```

   把开头约 500 字符的输出提供给作者以分析剩余结构。

## 重复执行

脚本是**幂等**的：

- 已清理的笔记顶部不再包含元数据标题，正则匹配会直接返回未命中，跳过修改。
- 未命中的笔记不会被写入。
- `noteId` 去重确保共享子树不会被多次扫描。

可以随时再次运行以确认状态。

## 风险与回滚

- 误删场景：只有在某篇笔记恰好以「同一标题节点 + `title:` / `date:` /
  `lastmod:`」开头时才会被错误清理。该模式与正文标题冲突的概率极低，
  但仍建议执行前启用版本控制或备份数据库。
- 恢复方式：从备份还原，或在笔记的 `Note Revisions` 中回滚到之前的版本。

## 相关文件

- `scripts/trilium-cleanup-imported-metadata.js`：清理脚本本体
- `README.md`：仓库总览，含导出器使用说明