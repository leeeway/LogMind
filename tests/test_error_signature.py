"""
Unit Tests for Semantic Dedup — Error Signature Extraction

Tests extract_error_signature(), the core function that determines
vector dedup quality. Signature changes directly affect whether
similar errors are detected as duplicates.
"""

import pytest
from logmind.domain.analysis.semantic_dedup import extract_error_signature


class TestJavaSignatures:
    """Tests for Java exception signature extraction."""

    def test_simple_exception(self):
        logs = """[2024-04-23 10:00:00] [ERROR] java.lang.NullPointerException
    at com.example.UserService.getUser(UserService.java:45)
    at com.example.Controller.handle(Controller.java:123)"""
        sig = extract_error_signature(logs, "java")
        assert "NullPointerException" in sig
        assert "EXCEPTIONS:" in sig

    def test_nested_exception(self):
        logs = """org.springframework.dao.DataIntegrityViolationException: could not execute statement
Caused by: java.sql.SQLIntegrityConstraintViolationException: Duplicate entry
    at com.mysql.cj.jdbc.ClientPreparedStatement.executeInternal"""
        sig = extract_error_signature(logs, "java")
        assert "DataIntegrityViolationException" in sig
        assert "SQLIntegrityConstraintViolationException" in sig

    def test_stack_frames_extracted(self):
        logs = """java.lang.RuntimeException: Connection refused
    at com.example.HttpClient.send(HttpClient.java:78)
    at com.example.OrderService.placeOrder(OrderService.java:156)
    at com.example.Controller.createOrder(Controller.java:89)"""
        sig = extract_error_signature(logs, "java")
        assert "STACK:" in sig
        assert "com.example.HttpClient.send" in sig

    def test_line_numbers_stripped(self):
        """Signatures should be line-number-agnostic."""
        logs1 = """NullPointerException
    at com.example.Foo.bar(Foo.java:10)"""
        logs2 = """NullPointerException
    at com.example.Foo.bar(Foo.java:999)"""
        sig1 = extract_error_signature(logs1, "java")
        sig2 = extract_error_signature(logs2, "java")
        # Both should produce the same signature (line number stripped)
        assert sig1 == sig2

    def test_max_stack_frames_limited(self):
        """At most 10 unique stack frames should be captured."""
        frames = "\n".join(
            f"    at com.example.Class{i}.method{i}(Class{i}.java:{i})"
            for i in range(20)
        )
        logs = f"java.lang.RuntimeException\n{frames}"
        sig = extract_error_signature(logs, "java")
        # Count method names in STACK section
        stack_part = sig.split("STACK: ")[1] if "STACK: " in sig else ""
        methods = stack_part.split(" → ")
        assert len(methods) <= 10


class TestCSharpSignatures:
    """Tests for C# exception signature extraction."""

    def test_dotnet_exception(self):
        logs = """System.NullReferenceException: Object reference not set
   at Gyyx.Core.UserService.GetUser(String userId) in D:\\projects\\Core\\UserService.cs:line 45"""
        sig = extract_error_signature(logs, "csharp")
        assert "NullReferenceException" in sig

    def test_inner_exception_chain(self):
        logs = """System.Data.SqlClient.SqlException: Timeout expired
--- End of inner exception stack trace ---
System.AggregateException: One or more errors occurred."""
        sig = extract_error_signature(logs, "csharp")
        assert "SqlException" in sig
        assert "AggregateException" in sig


class TestFallbackSignatures:
    """Tests for logs without structured exceptions."""

    def test_error_lines_fallback(self):
        logs = """[2024-04-23 10:00:00] [ERROR] Connection to database failed
[2024-04-23 10:00:01] [ERROR] Retry exhausted after 3 attempts"""
        sig = extract_error_signature(logs)
        assert "ERRORS:" in sig
        assert "Connection to database failed" in sig

    def test_raw_text_fallback(self):
        logs = "Something went wrong but no ERROR marker"
        sig = extract_error_signature(logs)
        assert len(sig) <= 300
        assert sig == logs

    def test_empty_input(self):
        assert extract_error_signature("") == ""
        assert extract_error_signature(None) == ""

    def test_short_input(self):
        sig = extract_error_signature("err")
        # Very short signatures are valid (may be filtered by caller)
        assert sig is not None


class TestSignatureStability:
    """Ensure signatures are stable across minor log variations."""

    def test_different_timestamps_same_sig(self):
        base = """java.lang.RuntimeException: DB timeout
    at com.example.Dao.query(Dao.java:55)"""
        logs1 = f"[2024-04-23 10:00:00] {base}"
        logs2 = f"[2024-04-24 15:30:00] {base}"
        assert extract_error_signature(logs1) == extract_error_signature(logs2)

    def test_different_thread_ids_same_sig(self):
        base = """NullPointerException
    at com.example.Handler.handle(Handler.java:12)"""
        logs1 = f"[thread-1] {base}"
        logs2 = f"[thread-99] {base}"
        assert extract_error_signature(logs1) == extract_error_signature(logs2)
