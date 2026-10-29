"""Simulated Gmail HTTP/state fixtures. These tests never contact a mail server."""
import base64
import copy
import json
import sqlite3
import tempfile
import time
import unittest
from email import policy
from email.parser import BytesParser
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from widemind.tools.contracts import HTTPResponse, ToolContext, ToolError, Unavailable, Uncertain
from widemind.tools.gmail import GmailAdapter, _decode
from widemind.tools.gmail_oauth import REQUIRED_SCOPES


class PrivateState:
    """Fixture store uses new SQLite connections to test durable-before-HTTP ordering."""
    def __init__(self, path):
        self.path = path
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS states (id TEXT PRIMARY KEY, data TEXT NOT NULL)")

    def load(self, action_id):
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT data FROM states WHERE id=?", (action_id,)).fetchone()
        return json.loads(row[0]) if row else {}

    def update(self, action_id, patch):
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM states WHERE id=?", (action_id,)).fetchone()
            data = json.loads(row[0]) if row else {}
            data.update(patch)
            db.execute("INSERT OR REPLACE INTO states VALUES (?,?)", (action_id, json.dumps(data)))

    def dispatched(self, action_id, intent):
        self.update(action_id, {"dispatch_intent": intent})


class Secrets:
    def __init__(self):
        self.data = {"gmail.client": "fixture-secret-never-log", "gmail.token": json.dumps({
            "access_token": "fixture-access-never-log", "refresh_token": "fixture-refresh-never-log",
            "expires_at": time.time() + 3600})}

    def get(self, name):
        return self.data.get(name)

    def set(self, name, value):
        self.data[name] = value


class Files:
    def __init__(self, root):
        self.root = root

    def path(self, path):
        candidate = (self.root / path).resolve()
        if not candidate.is_relative_to(self.root.resolve()) or candidate == self.root:
            raise ToolError("workspace_path", "Path escapes the workspace.")
        return candidate

    def read_bytes(self, path, *, max_bytes):
        data = self.path(path).read_bytes()
        if len(data) > max_bytes:
            raise ToolError("workspace_limit", "Workspace file is too large.")
        return data

    def write_bytes(self, path, data, *, overwrite=False):
        target = self.path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb" if overwrite else "xb") as f:
            f.write(data)
        return {"path": path, "size": len(data)}


class GmailFixture:
    """Provider fixture can accept a mutation and drop its response like a crash."""
    def __init__(self, state, action_id):
        self.state, self.action_id, self.calls = state, action_id, []
        self.sent, self.drafts = {}, {}
        self.drop_response = False
        self.hide_results = False
        self.override = None
        self.info = {"aud": "fixture-client", "scope": " ".join(REQUIRED_SCOPES), "sub": "fixture-subject"}
        self.account = "dedicated@example.test"
        self.read_payload = {"id": "read1", "threadId": "thread1", "labelIds": ["INBOX"],
                             "payload": {"mimeType": "text/plain", "headers": [{"name": "Subject", "value": "Private"}],
                                         "body": {"data": base64.urlsafe_b64encode(b"hello customer").decode(), "size": 14}}}

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        kwargs["cancel"]()
        parsed = urlsplit(url)
        path, query = parsed.path, parse_qs(parsed.query)
        if self.override:
            response = self.override(method, parsed, kwargs)
            if response is not None:
                status, payload = response
                return HTTPResponse(status, {}, json.dumps(payload).encode(), url)
        if parsed.hostname == "oauth2.googleapis.com" and path == "/tokeninfo":
            result = self.info
        elif path.endswith("/profile"):
            result = {"emailAddress": self.account}
        elif method == "POST" and (path.endswith("/messages/send") or path.endswith("/drafts")):
            receipt = PrivateState(self.state.path).load(self.action_id)
            assert receipt["gmail"]["attempted"]
            assert receipt["dispatch_intent"]["mime_sha256"] == receipt["gmail"]["mime_sha256"]
            body = json.loads(kwargs["body"])
            is_draft = path.endswith("/drafts")
            raw = body["message"]["raw"] if is_draft else body["raw"]
            message = {"id": "msg" + str(len(self.sent) + len(self.drafts) + 1), "threadId": "thread1", "raw": raw,
                       "labelIds": ["DRAFT"] if is_draft else ["SENT"]}
            if is_draft:
                result = {"id": "draft1", "message": message}
                self.drafts[result["id"]] = copy.deepcopy(result)
            else:
                self.sent[message["id"]] = copy.deepcopy(message)
                result = message
            if self.drop_response:
                raise TimeoutError("fixture-access-never-log private-customer-body")
        elif path.endswith("/messages") or path.endswith("/drafts"):
            is_draft = path.endswith("/drafts")
            source = self.drafts if is_draft else self.sent
            rows = [{"id": k, "message": {"id": v["message"]["id"]}} if is_draft else {"id": k, "threadId": v["threadId"]}
                    for k, v in source.items()] if not self.hide_results else []
            result = {"drafts" if is_draft else "messages": rows}
        elif "/attachments/" in path:
            result = {"data": base64.urlsafe_b64encode(b"private attachment").decode(), "size": 18}
        elif "/threads/" in path:
            result = {"id": "thread1", "messages": [self.read_payload]}
        elif "/drafts/" in path:
            result = self.drafts[path.rsplit("/", 1)[-1]]
        elif query.get("format") == ["raw"]:
            result = self.sent[path.rsplit("/", 1)[-1]]
        else:
            result = self.read_payload
        return HTTPResponse(200, {}, json.dumps(result).encode(), url)


class GmailTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.files = Files(self.root / "workspace")
        self.files.root.mkdir()
        self.state = PrivateState(self.root / "private.sqlite3")
        self.secrets = Secrets()
        self.config = {"client_id": "fixture-client", "redirect_uri": "http://127.0.0.1:8123/callback",
                       "expected_account": "dedicated@example.test", "expected_subject": "fixture-subject",
                       "client_secret_ref": "gmail.client", "token_ref": "gmail.token", "oauth_state_ref": "gmail.state"}
        self.transport = GmailFixture(self.state, "action1")
        self.ctx = ToolContext("action1", "run1", "reservation1", self.config,
                               {"profile": "sandbox", "allowed_email_recipients": ["controlled@example.test"]},
                               self.files, self.secrets, self.transport, self.state, time.monotonic() + 60, lambda: None)
        self.adapter = GmailAdapter()
        self.args = {"operation": "send", "to": ["controlled@example.test"], "subject": "Private order",
                     "text": "private-customer-body"}

    def mutations(self):
        return [(m, u) for m, u, _ in self.transport.calls if m == "POST" and urlsplit(u).hostname == "gmail.googleapis.com"]

    def test_send_persists_intended_mime_before_effect_and_completed_repeat_does_no_http(self):
        self.files.write_bytes("delivery.txt", b"customer deliverable")
        self.args.update(attachments=[{"path": "delivery.txt"}], html="<p>private-customer-body</p>")
        result = self.adapter.execute(self.args, self.ctx)
        self.assertTrue(result.data["provider_accepted"])
        self.assertFalse(result.data["delivery_confirmed"])
        self.assertEqual(result.usage.cost_microusd, 0)
        self.assertEqual(result.usage.units["send_attempts"], 1)
        state = PrivateState(self.state.path).load("action1")["gmail"]
        mime = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(state["raw"]))
        self.assertEqual(mime["From"], "dedicated@example.test")
        self.assertEqual(mime["Message-ID"], state["message_id"])
        self.assertEqual(next(mime.iter_attachments()).get_payload(decode=True), b"customer deliverable")
        self.assertNotIn("fixture-secret-never-log", json.dumps(state))
        calls = len(self.transport.calls)
        again = GmailAdapter().execute(self.args, self.ctx)
        self.assertEqual(again.data, result.data)
        self.assertEqual(len(self.transport.calls), calls)
        self.assertEqual(len(self.mutations()), 1)

    def test_accepted_send_with_lost_response_recovers_after_restart_without_second_send(self):
        self.transport.drop_response = True
        with self.assertRaises(Uncertain) as error:
            self.adapter.execute(self.args, self.ctx)
        self.assertNotIn("fixture-access-never-log", repr(error.exception))
        self.assertNotIn("private-customer-body", str(error.exception))
        self.assertEqual(error.exception.usage.units["send_attempts"], 1)
        self.ctx.state = PrivateState(self.state.path)
        recovered = GmailAdapter().reconcile(self.args, self.ctx)
        self.assertEqual(recovered.outcome, "completed")
        self.assertTrue(recovered.result.data["reconciled"])
        self.assertEqual(len(self.mutations()), 1)
        search = next(c for c in self.transport.calls if "rfc822msgid" in c[1])
        self.assertEqual(parse_qs(urlsplit(search[1]).query)["includeSpamTrash"], ["true"])

    def test_absent_search_does_not_prove_failure_and_execute_never_resends(self):
        self.transport.drop_response = self.transport.hide_results = True
        with self.assertRaises(Uncertain):
            self.adapter.execute(self.args, self.ctx)
        recovered = self.adapter.reconcile(self.args, self.ctx)
        self.assertEqual(recovered.outcome, "uncertain")
        self.assertEqual(recovered.result.usage.units["http_attempts"], len(self.transport.calls))
        with self.assertRaises(Uncertain):
            GmailAdapter().execute(self.args, self.ctx)
        self.assertEqual(len(self.mutations()), 1)

    def test_draft_creation_timeout_reconciles_draft_identity_without_recreating(self):
        self.args["operation"] = "draft"
        self.transport.drop_response = True
        with self.assertRaises(Uncertain):
            self.adapter.execute(self.args, self.ctx)
        recovery = GmailAdapter().reconcile(self.args, self.ctx)
        self.assertEqual(recovery.outcome, "completed")
        self.assertEqual(recovery.result.data["draft_id"], "draft1")
        self.assertNotEqual(recovery.result.data["draft_id"], recovery.result.data["message_id"])
        self.assertEqual(len(self.mutations()), 1)

    def test_search_candidates_tampered_body_wrong_label_or_multiple_ids_stay_uncertain(self):
        self.transport.drop_response = True
        with self.assertRaises(Uncertain):
            self.adapter.execute(self.args, self.ctx)
        original = copy.deepcopy(self.transport.sent)
        self.transport.sent["msg1"]["labelIds"] = ["INBOX"]
        self.assertEqual(self.adapter.reconcile(self.args, self.ctx).outcome, "uncertain")
        self.transport.sent = copy.deepcopy(original)
        raw = base64.urlsafe_b64decode(self.transport.sent["msg1"]["raw"]).replace(b"private-customer-body", b"modified-message-body")
        self.transport.sent["msg1"]["raw"] = base64.urlsafe_b64encode(raw).decode()
        self.assertEqual(self.adapter.reconcile(self.args, self.ctx).outcome, "uncertain")
        self.transport.sent = copy.deepcopy(original)
        self.transport.sent["msg2"] = copy.deepcopy(original["msg1"])
        self.assertEqual(self.adapter.reconcile(self.args, self.ctx).outcome, "uncertain")
        self.assertEqual(len(self.mutations()), 1)

    def test_reused_action_with_changed_request_or_account_is_rejected_without_http(self):
        self.adapter.execute(self.args, self.ctx)
        count = len(self.transport.calls)
        for change in ({"subject": "different"}, {"to": ["controlled@example.test"], "text": "different"}):
            with self.assertRaises(ToolError) as error:
                self.adapter.execute(dict(self.args, **change), self.ctx)
            self.assertEqual(error.exception.code, "action_conflict")
        self.config["expected_account"] = "unrelated@example.test"
        with self.assertRaises(ToolError):
            self.adapter.execute(self.args, self.ctx)
        self.assertEqual(len(self.transport.calls), count)

    def test_wrong_account_audience_grants_or_missing_secrets_never_mutate(self):
        variants = [("account", "unrelated@example.test"), ("aud", "unrelated-client"), ("scope", REQUIRED_SCOPES[0])]
        for key, value in variants:
            with self.subTest(key=key):
                if key == "account":
                    self.transport.account = value
                else:
                    self.transport.info[key] = value
                with self.assertRaises(Unavailable):
                    self.adapter.execute(self.args, self.ctx)
                self.transport.account = "dedicated@example.test"
                self.transport.info.update(aud="fixture-client", scope=" ".join(REQUIRED_SCOPES))
        self.secrets.data.clear()
        with self.assertRaises(Unavailable):
            self.adapter.execute(self.args, self.ctx)
        self.assertFalse(self.mutations())
        self.assertNotIn("gmail", self.state.load("action1"))
        self.assertNotIn("dispatch_intent", self.state.load("action1"))
        self.assertEqual(self.state.load("action1")["gmail_usage"]["send_attempts"], 0)

    def test_all_recipients_including_bcc_require_owner_policy(self):
        self.args["bcc"] = ["unrelated@example.test"]
        for profile in ("development", "sandbox", "production"):
            self.ctx.policy = {"profile": profile, "allow_any_email_recipient": profile != "production",
                               "allowed_email_recipients": ["controlled@example.test"]}
            with self.assertRaises(ToolError) as error:
                self.adapter.execute(self.args, self.ctx)
            self.assertEqual(error.exception.code, "recipient_not_allowed")
        self.assertFalse(self.transport.calls)
        self.ctx.policy = {"profile": "production", "allow_any_email_recipient": True}
        self.adapter.execute(self.args, self.ctx)
        self.assertEqual(len(self.mutations()), 1)

    def test_invalid_inputs_and_header_injection_rejected_before_http(self):
        invalid = [{"subject": "subject\r\nBcc: unrelated@example.test"}, {"to": ["Display <controlled@example.test>"]},
                   {"references": ["<id@example.test>\nBcc:x"]}, {"operation": []}, {"text": "\ud800"},
                   {"attachments": [{"path": "x", "content_type": 123}]}, {"attachments": [{"path": "x", "filename": "../x"}]}]
        for values in invalid:
            with self.subTest(values=values):
                with self.assertRaises(ToolError):
                    self.adapter.execute(dict(self.args, **values), self.ctx)
        self.assertFalse(self.transport.calls)

    def test_workspace_attachment_escape_or_symlink_fails_before_dispatch(self):
        (self.root / "secret.txt").write_text("fixture-secret-never-log")
        (self.files.root / "link.txt").symlink_to(self.root / "secret.txt")
        for path in ("../secret.txt", "link.txt"):
            self.args["attachments"] = [{"path": path}]
            with self.assertRaises(ToolError):
                self.adapter.execute(self.args, self.ctx)
        self.assertFalse(self.mutations())
        self.assertNotIn("gmail", self.state.load("action1"))
        self.assertNotIn("dispatch_intent", self.state.load("action1"))

    def test_list_paginates_and_exposes_truncation_and_empty_page(self):
        def lists(method, parsed, kwargs):
            if parsed.path.endswith("/messages"):
                page = parse_qs(parsed.query).get("pageToken")
                return 200, {"messages": [{"id": "msg1", "threadId": "thread1"}], "nextPageToken": "page2"} if not page else {"messages": []}
        self.transport.override = lists
        result = self.adapter.execute({"operation": "list", "max_pages": 1, "query": "is:unread"}, self.ctx)
        self.assertTrue(result.data["truncated"])
        result = self.adapter.execute({"operation": "list", "max_pages": 2}, self.ctx)
        self.assertFalse(result.data["truncated"])
        self.assertEqual(result.data["pages"], 2)
        self.assertEqual(result.data["messages"], [{"id": "msg1", "thread_id": "thread1"}])

    def test_read_and_thread_decode_private_mime_and_attachment_writes_workspace_only(self):
        result = self.adapter.execute({"operation": "read", "message_id": "read1"}, self.ctx)
        self.assertEqual(result.data["message"]["body_parts"][0]["text"], "hello customer")
        self.assertEqual(result.trust, "untrusted")
        thread = self.adapter.execute({"operation": "thread", "thread_id": "thread1"}, self.ctx)
        self.assertEqual(thread.data["messages"][0]["id"], "read1")
        attachment = {"operation": "attachment", "message_id": "read1", "attachment_id": "long-id-with-symbols=", "output_path": "received/file.bin"}
        result = self.adapter.execute(attachment, self.ctx)
        self.assertEqual((self.files.root / "received/file.bin").read_bytes(), b"private attachment")
        self.assertEqual(result.artifacts[0]["path"], "received/file.bin")
        self.assertNotIn("private attachment", repr(result))

    def test_explicit_provider_rejection_is_recorded_but_no_retry_occurs(self):
        def reject(method, parsed, kwargs):
            if method == "POST" and parsed.path.endswith("/messages/send"):
                return 403, {"error": {"code": 403, "message": "fixture-access-never-log private-customer-body"}}
        self.transport.override = reject
        with self.assertRaises(ToolError) as error:
            self.adapter.execute(self.args, self.ctx)
        self.assertNotIn("fixture-access-never-log", str(error.exception))
        self.assertNotIn("private-customer-body", repr(error.exception))
        self.assertEqual(self.adapter.reconcile(self.args, self.ctx).outcome, "not_executed")
        self.assertEqual(len(self.mutations()), 1)

    def test_read_and_profile_failures_carry_measured_attempts_and_sanitized_errors(self):
        self.transport.info["aud"] = "wrong-client"
        with self.assertRaises(Unavailable) as failure:
            self.adapter.execute({"operation": "profile"}, self.ctx)
        self.assertEqual(failure.exception.usage.units["http_attempts"], 1)
        self.assertEqual(failure.exception.usage.cost_microusd, 0)
        self.transport.info["aud"] = "fixture-client"
        self.transport.read_payload["payload"]["headers"] = None
        with self.assertRaises(ToolError) as failure:
            self.adapter.execute({"operation": "read", "message_id": "read1"}, self.ctx)
        self.assertEqual(failure.exception.usage.units["http_attempts"], len(self.transport.calls))
        self.assertEqual(failure.exception.usage.units["http_attempts"], 4)
        recovered = self.adapter.reconcile({"operation": "read", "message_id": "read1"}, self.ctx)
        self.assertEqual(recovered.outcome, "not_executed")
        self.assertEqual(recovered.result.usage.units["http_attempts"], 4)
        self.assertNotIn("fixture-access-never-log", str(failure.exception))

    def test_attachment_commit_without_completed_receipt_stays_uncertain_and_never_rewrites(self):
        self.ctx.job_id = "customer-job"
        args = {"operation": "attachment", "message_id": "read1", "attachment_id": "attachment1", "output_path": "received/private.bin"}
        original = self.files.write_bytes
        writes = []

        def commit_then_interrupt(path, content, **kwargs):
            intent = PrivateState(self.state.path).load("action1")["local_intent"]
            self.assertEqual(intent["output_path"], path)
            self.assertEqual(intent["job_id"], "customer-job")
            self.assertEqual(intent["bytes"], len(content))
            writes.append(path)
            original(path, content, **kwargs)
            raise ToolError("cancelled", "Fixture stopped after the local commit.")

        self.files.write_bytes = commit_then_interrupt
        with self.assertRaises(ToolError) as failure:
            self.adapter.execute(args, self.ctx)
        self.assertEqual(failure.exception.usage.units["http_attempts"], 3)
        self.assertEqual((self.files.root / args["output_path"]).read_bytes(), b"private attachment")
        calls = len(self.transport.calls)
        self.ctx.state = PrivateState(self.state.path)
        recovered = GmailAdapter().reconcile(args, self.ctx)
        self.assertEqual(recovered.outcome, "uncertain")
        self.assertEqual(recovered.result.usage.units["http_attempts"], 3)
        with self.assertRaises(Uncertain) as failure:
            GmailAdapter().execute(args, self.ctx)
        self.assertEqual(failure.exception.usage.units["http_attempts"], 3)
        self.assertEqual(len(self.transport.calls), calls)
        self.assertEqual(writes, [args["output_path"]])

    def test_attachment_completed_receipt_recovers_after_cancel_with_job_hash_and_no_http(self):
        self.ctx.job_id = "customer-job"
        args = {"operation": "attachment", "message_id": "read1", "attachment_id": "attachment1", "output_path": "received/private.bin"}
        self.ctx.cancelled = lambda: bool(self.state.load("action1").get("gmail_attachment", {}).get("completed"))
        with self.assertRaises(ToolError) as failure:
            self.adapter.execute(args, self.ctx)
        self.assertEqual(failure.exception.code, "cancelled")
        self.assertEqual(failure.exception.usage.units["http_attempts"], 3)
        self.ctx.cancelled = lambda: False
        self.ctx.state = PrivateState(self.state.path)
        calls = len(self.transport.calls)
        recovered = GmailAdapter().reconcile(args, self.ctx)
        self.assertEqual(recovered.outcome, "completed")
        artifact = recovered.result.artifacts[0]
        self.assertEqual(artifact["job_id"], "customer-job")
        self.assertEqual(artifact["visibility"], "private")
        self.assertEqual(artifact["sha256"], recovered.result.data["sha256"])
        self.assertEqual(recovered.result.usage.units["http_attempts"], 3)
        self.assertEqual(GmailAdapter().execute(args, self.ctx).artifacts, [artifact])
        self.ctx.job_id = "unrelated-job"
        self.assertEqual(GmailAdapter().reconcile(args, self.ctx).outcome, "uncertain")
        self.assertEqual(len(self.transport.calls), calls)

    def test_attachment_missing_or_tampered_receipt_never_proves_not_executed(self):
        args = {"operation": "attachment", "message_id": "read1", "attachment_id": "attachment1", "output_path": "received/file.bin"}
        empty = self.adapter.reconcile(args, self.ctx)
        self.assertEqual(empty.outcome, "uncertain")
        self.assertEqual(empty.result.usage.units["http_attempts"], 0)
        self.adapter.execute(args, self.ctx)
        receipt = self.state.load("action1")["gmail_attachment"]
        receipt["artifacts"][0]["sha256"] = "0" * 64
        self.state.update("action1", {"gmail_attachment": receipt})
        recovered = self.adapter.reconcile(args, self.ctx)
        self.assertEqual(recovered.outcome, "uncertain")
        self.assertEqual(recovered.result.usage.units["http_attempts"], 3)

    def test_timeout_cancel_and_size_bounds_are_applied_to_every_request(self):
        self.adapter.execute({"operation": "profile"}, self.ctx)
        self.assertTrue(self.transport.calls)
        for _, _, kwargs in self.transport.calls:
            self.assertGreater(kwargs["timeout"], 0)
            self.assertLessEqual(kwargs["timeout"], 30)
            self.assertGreater(kwargs["max_bytes"], 0)
            self.assertTrue(callable(kwargs["cancel"]))
        count = len(self.transport.calls)
        self.ctx.cancelled = lambda: True
        with self.assertRaises(ToolError):
            self.adapter.execute(self.args, self.ctx)
        self.assertEqual(len(self.transport.calls), count)
        with self.assertRaises(ToolError):
            _decode("%%%%", 100)

    def test_oversized_reply_headers_and_malformed_provider_messages_fail_safely(self):
        huge = "<" + "a" * 1200 + "@example.test>"
        for values in ({"in_reply_to": huge}, {"references": [huge]}):
            with self.assertRaises(ToolError):
                self.adapter.quote(dict(self.args, **values), self.config)
        self.assertFalse(self.transport.calls)

        def malformed(method, parsed, kwargs):
            if "/threads/" in parsed.path:
                return 200, {"id": "thread1", "messages": [None]}
            if "/messages/" in parsed.path:
                return 200, {"id": "read1", "payload": {"mimeType": "text/plain", "headers": None, "body": {}}}
        self.transport.override = malformed
        for request in ({"operation": "thread", "thread_id": "thread1"}, {"operation": "read", "message_id": "read1"}):
            with self.assertRaises(ToolError) as failure:
                self.adapter.execute(request, self.ctx)
            self.assertEqual(failure.exception.code, "gmail_invalid_response")


if __name__ == "__main__":
    unittest.main()
