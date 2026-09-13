"""进度输出测试。"""

import io

from src.progress import ProgressReporter


def test_progress_update_renders_progress_line():
    stream = io.StringIO()
    reporter = ProgressReporter(enabled=True, stream=stream)
    reporter.update("siyuan.scan", 3, 10, "test detail")
    output = stream.getvalue()
    assert "siyuan.scan" in output
    assert "3/10" in output
    assert "test detail" in output


def test_progress_disabled_renders_nothing():
    stream = io.StringIO()
    reporter = ProgressReporter(enabled=False, stream=stream)
    reporter.update("siyuan.scan", 1, 10, "x")
    reporter.finish()
    assert stream.getvalue() == ""


def test_progress_finish_writes_newline():
    stream = io.StringIO()
    reporter = ProgressReporter(enabled=True, stream=stream)
    reporter.update("convert", 1, 1, "done")
    reporter.finish("ok")
    output = stream.getvalue()
    assert output.endswith("\n")
    assert "ok" in output