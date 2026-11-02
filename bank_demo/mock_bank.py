import json
import secrets
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


DEMO_CUSTOMER_ID = "demo-customer"
DEMO_CUSTOMER_PASSWORD = "demo-password"
DEMO_AGENT_ID = "demo-agent"
MAX_AGENT_LIMIT_CENTS = 5000
TOKEN_LIFETIME_SECONDS = 900
AGENT_SCOPES = frozenset(("balance:read", "transfer:request"))


class DemoBank:
    def __init__(self):
        self.balance_cents = 250000
        self.pending_approvals = {}
        self.transfers = []
        self.tokens = {}

    def issue_token(self, **claims):
        token = secrets.token_urlsafe(32)
        self.tokens[token] = dict(
            claims, expires_at=time.time() + TOKEN_LIFETIME_SECONDS
        )
        return token

    def authenticate_customer(self, customer_id, password):
        if customer_id != DEMO_CUSTOMER_ID or password != DEMO_CUSTOMER_PASSWORD:
            return None
        return self.issue_token(
            role="customer",
            customer_id=customer_id,
            scopes=("agent:grant", "approval:read", "approval:decide"),
        )


def make_handler(bank):
    class Handler(BaseHTTPRequestHandler):
        def _send_json(self, status, result):
            body = json.dumps(result).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._send_json(400, {"error": "invalid_content_length"})
                return None
            if length <= 0 or length > 16384:
                self._send_json(400, {"error": "invalid_request_size"})
                return None
            try:
                value = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid_json"})
                return None
            if not isinstance(value, dict):
                self._send_json(400, {"error": "json_object_required"})
                return None
            return value

        def _authorize(self, expected):
            authorization = self.headers.get("Authorization", "")
            scheme, separator, token = authorization.partition(" ")
            claims = bank.tokens.get(token) if scheme == "Bearer" and separator else None
            if claims is None or claims["expires_at"] <= time.time():
                self._send_json(401, {"error": "invalid_or_expired_token"})
                return None
            if claims["role"] != expected:
                self._send_json(403, {"error": "not_authorized_for_this_action"})
                return None
            return claims

        def _require_scope(self, claims, scope):
            if scope in claims["scopes"]:
                return True
            self._send_json(403, {"error": "required_scope_missing", "scope": scope})
            return False

        def do_GET(self):
            if self.path == "/v1/accounts/demo/balance":
                claims = self._authorize("agent")
                if claims is None or not self._require_scope(claims, "balance:read"):
                    return
                self._send_json(200, {"balance_cents": bank.balance_cents})
                return

            parts = self.path.strip("/").split("/")
            if len(parts) == 3 and parts[:2] == ["v1", "approvals"]:
                claims = self._authorize("customer")
                if claims is None or not self._require_scope(claims, "approval:read"):
                    return
                pending = bank.pending_approvals.get(parts[2])
                if pending is None or pending["customer_id"] != claims["customer_id"]:
                    self._send_json(404, {"error": "approval_not_found"})
                    return
                self._send_json(
                    200,
                    {
                        "approval_id": parts[2],
                        "recipient": pending["recipient"],
                        "amount_cents": pending["amount_cents"],
                        "status": pending["status"],
                    },
                )
                return

            self._send_json(404, {"error": "not_found"})

        def do_POST(self):
            if self.path == "/v1/auth/customer/sessions":
                body = self._read_json()
                if body is None:
                    return
                customer_id = body.get("customer_id")
                password = body.get("password")
                if not isinstance(customer_id, str) or not isinstance(password, str):
                    self._send_json(400, {"error": "customer_id_and_password_required"})
                    return
                token = bank.authenticate_customer(customer_id, password)
                if token is None:
                    self._send_json(401, {"error": "invalid_customer_credentials"})
                    return
                self._send_json(
                    200,
                    {
                        "access_token": token,
                        "token_type": "Bearer",
                        "expires_in": TOKEN_LIFETIME_SECONDS,
                    },
                )
                return

            if self.path == "/v1/auth/agent-grants":
                claims = self._authorize("customer")
                if claims is None or not self._require_scope(claims, "agent:grant"):
                    return
                body = self._read_json()
                if body is None:
                    return
                agent_id = body.get("agent_id")
                scopes = body.get("scopes")
                limit = body.get("max_transfer_cents")
                if (
                    agent_id != DEMO_AGENT_ID
                    or not isinstance(scopes, list)
                    or not scopes
                    or any(not isinstance(scope, str) for scope in scopes)
                    or not set(scopes).issubset(AGENT_SCOPES)
                    or not isinstance(limit, int)
                    or isinstance(limit, bool)
                    or not 1 <= limit <= MAX_AGENT_LIMIT_CENTS
                ):
                    self._send_json(400, {"error": "invalid_agent_grant"})
                    return
                token = bank.issue_token(
                    role="agent",
                    customer_id=claims["customer_id"],
                    agent_id=agent_id,
                    scopes=tuple(set(scopes)),
                    max_transfer_cents=limit,
                )
                self._send_json(
                    201,
                    {
                        "access_token": token,
                        "token_type": "Bearer",
                        "expires_in": TOKEN_LIFETIME_SECONDS,
                        "agent_id": agent_id,
                        "scopes": sorted(set(scopes)),
                        "max_transfer_cents": limit,
                    },
                )
                return

            if self.path == "/v1/transfers":
                claims = self._authorize("agent")
                if claims is None or not self._require_scope(claims, "transfer:request"):
                    return
                body = self._read_json()
                if body is None:
                    return
                recipient = body.get("recipient")
                amount_cents = body.get("amount_cents")
                if (
                    not isinstance(recipient, str)
                    or not recipient.strip()
                    or not isinstance(amount_cents, int)
                    or isinstance(amount_cents, bool)
                    or amount_cents <= 0
                ):
                    self._send_json(400, {"error": "invalid_transfer"})
                    return
                if amount_cents > claims["max_transfer_cents"]:
                    self._send_json(
                        403,
                        {
                            "error": "agent_transfer_limit_exceeded",
                            "limit_cents": claims["max_transfer_cents"],
                        },
                    )
                    return
                recipient = recipient.strip()
                if recipient.casefold() != "coffee shop":
                    approval_id = str(uuid.uuid4())
                    bank.pending_approvals[approval_id] = {
                        "recipient": recipient,
                        "amount_cents": amount_cents,
                        "status": "pending",
                        "customer_id": claims["customer_id"],
                        "agent_id": claims["agent_id"],
                    }
                    self._send_json(
                        202,
                        {
                            "status": "customer_approval_required",
                            "approval_id": approval_id,
                        },
                    )
                    return
                transfer = self._execute_transfer(recipient, amount_cents)
                self._send_json(201, transfer)
                return

            parts = self.path.strip("/").split("/")
            if len(parts) == 3 and parts[:2] == ["v1", "approvals"]:
                claims = self._authorize("customer")
                if claims is None or not self._require_scope(claims, "approval:decide"):
                    return
                body = self._read_json()
                if body is None:
                    return
                decision = body.get("decision")
                if decision not in ("approve", "reject"):
                    self._send_json(400, {"error": "decision_must_be_approve_or_reject"})
                    return
                pending = bank.pending_approvals.get(parts[2])
                if pending is None or pending["customer_id"] != claims["customer_id"]:
                    self._send_json(404, {"error": "approval_not_found"})
                    return
                if pending["status"] != "pending":
                    self._send_json(409, {"error": "approval_already_decided"})
                    return
                if decision == "reject":
                    pending["status"] = "rejected"
                    self._send_json(
                        200,
                        {"approval_id": parts[2], "status": "rejected"},
                    )
                    return
                pending["status"] = "approved"
                transfer = self._execute_transfer(
                    pending["recipient"], pending["amount_cents"]
                )
                self._send_json(
                    200,
                    {
                        "approval_id": parts[2],
                        "status": "approved",
                        "transfer_id": transfer["transfer_id"],
                    },
                )
                return

            self._send_json(404, {"error": "not_found"})

        def _execute_transfer(self, recipient, amount_cents):
            bank.balance_cents -= amount_cents
            transfer = {
                "transfer_id": str(uuid.uuid4()),
                "recipient": recipient,
                "amount_cents": amount_cents,
                "status": "completed",
            }
            bank.transfers.append(transfer)
            return transfer

        def log_message(self, format_string, *args):
            print("{} - {}".format(self.address_string(), format_string % args))

    return Handler


def make_server(host="127.0.0.1", port=8000):
    bank = DemoBank()
    server = ThreadingHTTPServer((host, port), make_handler(bank))
    server.demo_bank = bank
    return server


def main():
    server = make_server()
    host, port = server.server_address
    print("Demo-only mock bank listening at http://{}:{}".format(host, port))
    print("Use synthetic demo tokens only; do not expose this server to the network.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping mock bank.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
