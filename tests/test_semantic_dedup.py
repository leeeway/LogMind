"""
Tests for SemanticDedupStage — error signature extraction.

Covers:
  - Java exception signature extraction
  - C# exception signature extraction
  - Multi-exception merging
  - Non-exception error line extraction
  - Short/empty input handling
  - Stack frame line-number agnosticism
"""

import pytest
from logmind.domain.analysis.semantic_dedup import extract_error_signature


class TestExtractErrorSignature:
    """Tests for extract_error_signature()."""

    def test_java_single_exception(self):
        """Extracts Java exception class and stack frames."""
        logs = (
            "[2026-04-24 10:00:00] [ERROR] java.lang.NullPointerException: value is null\n"
            "  at com.example.service.UserService.getUser(UserService.java:42)\n"
            "  at com.example.controller.UserController.handle(UserController.java:18)\n"
        )
        sig = extract_error_signature(logs, "java")

        assert "NullPointerException" in sig
        assert "com.example.service.UserService.getUser" in sig
        assert "EXCEPTIONS:" in sig
        assert "STACK:" in sig
        # Line numbers should NOT appear (agnostic)
        assert ":42" not in sig

    def test_java_multiple_exceptions(self):
        """Multiple exception classes are all captured and sorted."""
        logs = (
            "org.springframework.dao.DataAccessException: DB error\n"
            "  at com.example.dao.UserDao.findById(UserDao.java:55)\n"
            "Caused by: java.sql.SQLException: Connection refused\n"
            "  at com.mysql.cj.jdbc.ConnectionImpl.connect(ConnectionImpl.java:120)\n"
        )
        sig = extract_error_signature(logs, "java")

        assert "DataAccessException" in sig
        assert "SQLException" in sig
        # Sorted by full qualified name: java.sql < org.springframework
        assert sig.index("SQLException") < sig.index("DataAccessException")

    def test_csharp_exception(self):
        """Extracts C# exception class names."""
        logs = (
            "System.InvalidOperationException: Operation is not valid\n"
            "  at Gyyx.Core.Service.Process() in D:\\src\\Service.cs:line 96\n"
        )
        sig = extract_error_signature(logs, "csharp")

        assert "InvalidOperationException" in sig

    def test_no_exception_falls_back_to_error_lines(self):
        """When no exception class is found, extracts ERROR lines."""
        logs = (
            "[2026-04-24] [ERROR] Connection to Redis timed out after 30s\n"
            "[2026-04-24] [INFO] Retrying connection...\n"
            "[2026-04-24] [ERROR] Redis cluster unreachable\n"
        )
        sig = extract_error_signature(logs, "java")

        assert "ERRORS:" in sig
        assert "Connection to Redis timed out" in sig
        assert "Redis cluster unreachable" in sig
        # INFO lines should not be included
        assert "Retrying" not in sig

    def test_empty_input_returns_empty(self):
        """Empty input returns empty string."""
        assert extract_error_signature("", "java") == ""
        assert extract_error_signature("", "csharp") == ""

    def test_short_input_returns_raw(self):
        """Very short input without exceptions returns truncated raw text."""
        short_log = "some random log line"
        sig = extract_error_signature(short_log, "java")
        assert sig == short_log[:300]

    def test_stack_frame_line_number_agnostic(self):
        """Same exception with different line numbers produces same signature."""
        logs_v1 = (
            "java.lang.NullPointerException\n"
            "  at com.example.Foo.bar(Foo.java:42)\n"
            "  at com.example.Baz.qux(Baz.java:99)\n"
        )
        logs_v2 = (
            "java.lang.NullPointerException\n"
            "  at com.example.Foo.bar(Foo.java:100)\n"
            "  at com.example.Baz.qux(Baz.java:200)\n"
        )
        sig_v1 = extract_error_signature(logs_v1, "java")
        sig_v2 = extract_error_signature(logs_v2, "java")

        assert sig_v1 == sig_v2

    def test_max_stack_methods_limited(self):
        """Stack frames are limited to 10 unique methods."""
        lines = ["java.lang.RuntimeException: test\n"]
        for i in range(20):
            lines.append(f"  at com.example.Class{i}.method(Class{i}.java:{i})\n")
        logs = "".join(lines)

        sig = extract_error_signature(logs, "java")

        # Should contain STACK section but limited frames
        assert "STACK:" in sig
        # At most 8 methods in the output (sliced to [:8] in code)
        stack_part = sig.split("STACK: ")[1]
        method_count = stack_part.count("→") + 1
        assert method_count <= 8

    def test_error_fault_throwable_variants(self):
        """Handles Error, Fault, and Throwable class patterns."""
        for exc_class in [
            "java.lang.OutOfMemoryError",
            "System.ServiceModel.FaultException",
            "java.lang.Throwable",
        ]:
            logs = f"{exc_class}: something went wrong\n"
            sig = extract_error_signature(logs, "java")
            assert exc_class.split(".")[-1] in sig
