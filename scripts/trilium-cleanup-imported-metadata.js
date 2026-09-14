/**
 * TriliumNext - 全库清理迁移产生的顶部元数据
 *
 * 适用场景：
 * 从思源笔记通过 Markdown ZIP 导入到 Trilium 后，每篇文档顶部会带上一段
 * 由 YAML front matter 转换而来的内容，结构形如：
 *
 *     <h2>title: flag
 *     date: 2023-02-28T12:50:55+08:00
 *     lastmod: 2025-02-07T22:51:42+08:00</h2>
 *     <p></p>
 *
 * 整个 title / date / lastmod 元数据被包在第一个 `<h1>` ~ `<h6>` 里，
 * 后面还可能跟一个空白 `<p>` 或 `<hr>`。本脚本只扫描并删除这一段顶部
 * 元数据，不重建整篇文档，也不会触碰正文里的同名字面量。
 *
 * 处理顺序：
 *   1. 删除顶部包含 title / date / lastmod 的 h1 ~ h6 节点
 *   2. 删除紧随其后的空白 `<p>`
 *   3. 删除紧随其后的 `<hr>`
 *   4. 删除 HR 后可能残留的空白 `<p>`
 *
 * 使用方式：
 *   - 在 Trilium 主笔记上右键 → "Note navigation" → "Open JS backend"
 *     （或 "Advanced" → "Code notes" 下的 JS Backend 标签）
 *   - 新建一个名为 `Cleanup Imported Metadata` 的笔记，
 *     编程语言选择 "JavaScript (backend)"
 *   - 粘贴本脚本全文并保存
 *   - 在脚本笔记的工具栏上点击 "Run" 执行
 *
 * 推荐在执行前先备份 Trilium 数据库或开启版本控制，避免误删。
 */

const visited = new Set();

let scannedCount = 0;
let matchedCount = 0;
let cleanedCount = 0;
let skippedCount = 0;
let errorCount = 0;


/**
 * 判断一个段落片段是否为空。
 *
 * 兼容以下情况：
 *
 * <p></p>
 * <p><br></p>
 * <p>&nbsp;</p>
 * <p>‍</p>
 *
 * 最后一个是用户实际数据中常见的零宽字符（U+200B）。
 */
function isEmptyParagraph(html) {
    if (!html) {
        return false;
    }

    const text = html
        .replace(/<[^>]*>/g, "")
        .replace(/&nbsp;/gi, "")
        .replace(/&#160;/gi, "")
        .replace(/[\s\u200B\u200C\u200D\uFEFF]/g, "");

    return text.length === 0;
}


/**
 * 清理单篇笔记的 HTML 内容。
 *
 * 返回 { changed, content }，changed 表示是否实际修改了内容。
 */
function cleanContent(content) {

    if (!content || typeof content !== "string") {
        return {
            changed: false,
            content
        };
    }

    let result = content;


    /*
     * 1. 删除顶部元数据标题
     *
     * 匹配 h1 ~ h6 中包含 title: / date: / lastmod: 的节点，
     * 必须三者同时出现才视为元数据，避免误删正文标题。
     */
    const metadataHeadingRegex =
        /^\s*<h([1-6])(?:\s[^>]*)?>[\s\S]*?title\s*:[\s\S]*?date\s*:[\s\S]*?lastmod\s*:[\s\S]*?<\/h\1>/i;

    if (!metadataHeadingRegex.test(result)) {
        return {
            changed: false,
            content
        };
    }

    matchedCount++;

    // 删除整个 h1~h6 节点
    result = result.replace(
        metadataHeadingRegex,
        ""
    );

    // 去掉开头残留的空白
    result = result.replace(/^\s+/, "");


    /*
     * 2. 删除标题之后连续出现的空白 <p>
     *
     * 不能简单用正则删除所有 p，
     * 因为真正的正文首段也可能就是 <p>。
     */
    while (true) {

        const match = result.match(
            /^\s*(<p(?:\s[^>]*)?>[\s\S]*?<\/p>)/i
        );

        if (!match) {
            break;
        }

        const paragraph = match[1];

        if (!isEmptyParagraph(paragraph)) {
            break;
        }

        result = result.substring(match[0].length);
    }


    /*
     * 3. 删除顶部可能的 <hr>
     *
     * Markdown 中的 --- 导入后可能产生 <hr>、<hr/> 或 <hr />。
     */
    result = result.replace(
        /^\s*<hr(?:\s[^>]*)?\/?>\s*/i,
        ""
    );


    /*
     * 4. HR 之后可能还有空白段落，再清理一次。
     */
    while (true) {

        const match = result.match(
            /^\s*(<p(?:\s[^>]*)?>[\s\S]*?<\/p>)/i
        );

        if (!match) {
            break;
        }

        const paragraph = match[1];

        if (!isEmptyParagraph(paragraph)) {
            break;
        }

        result = result.substring(match[0].length);
    }

    result = result.replace(/^\s+/, "");

    return {
        changed: result !== content,
        content: result
    };
}


/**
 * 递归处理笔记及其子笔记。
 *
 * 使用 noteId 去重，避免共享子树被重复处理。
 */
function processNote(note) {

    if (!note) {
        return;
    }

    if (visited.has(note.noteId)) {
        return;
    }

    visited.add(note.noteId);

    scannedCount++;

    try {

        // 只处理 Text Note；其它类型（Code / File / Image 等）跳过。
        if (note.type === "text") {

            const content = note.getContent();

            if (
                typeof content === "string" &&
                content.length > 0
            ) {

                const result = cleanContent(content);

                if (result.changed) {

                    note.setContent(result.content);

                    cleanedCount++;

                    api.log(
                        `[已清理] ${note.title} (${note.noteId})`
                    );
                }
            }

        } else {

            skippedCount++;
        }


        // 递归处理子笔记
        const children = note.getChildNotes();

        for (const child of children) {

            processNote(child);

        }

    } catch (error) {

        errorCount++;

        api.log(
            `[失败] ${note.title} (${note.noteId})：${error.message}`
        );
    }
}


// =====================================================
// 开始全库清理
// =====================================================

api.log("");
api.log("======================================");
api.log("开始全库清理");
api.log("======================================");

const rootNote = api.getNote("root");

if (!rootNote) {
    throw new Error("无法获取 root 笔记");
}

const children = rootNote.getChildNotes();

for (const child of children) {
    processNote(child);
}

api.log("");
api.log("======================================");
api.log("全库清理完成");
api.log(`扫描：${scannedCount}`);
api.log(`匹配：${matchedCount}`);
api.log(`清理：${cleanedCount}`);
api.log(`跳过：${skippedCount}`);
api.log(`失败：${errorCount}`);
api.log("======================================");

return {
    scanned: scannedCount,
    matched: matchedCount,
    cleaned: cleanedCount,
    skipped: skippedCount,
    errors: errorCount
};