# Customer-Agent Bank Demo

A small, local-only hackathon demo of a customer-authorized agent. It uses Python's
standard library and synthetic data; it does not connect to a real bank.

## Run it

In terminal 1, start the mock bank API:

```sh
python3 -m bank_demo.mock_bank
```

In terminal 2, run the interactive flow:

```sh
python3 -m bank_demo.customer_agent demo
```

When prompted, use the seeded mock customer. The simulator then displays the
agent's requested permissions and limit; enter `y` to grant the access:

```text
Customer ID: demo-customer
Demo password: demo-password
Grant this agent access? [y/N] y
```

The password is entered without being echoed.

The demo reads a balance, makes an allowed payment, demonstrates the agent's
transfer limit, then requests a payment to a new recipient. The mock bank pauses
that payment. The customer sees the exact recipient and amount and must confirm
it in the terminal before the mock bank executes it. Pressing Enter rejects it.
Pressing Enter at the agent access prompt declines the grant.

Other simulator commands:

```sh
python3 -m bank_demo.customer_agent balance
python3 -m bank_demo.customer_agent pay --to "Coffee Shop" --amount 12.50
```

## Demo security boundaries

- The mock authentication endpoints verify the seeded demo customer and issue
  random, short-lived bearer tokens. The customer token requests an agent grant;
  that grant issues a separate agent token with `balance:read` and
  `transfer:request` permissions and a $50 limit.
- The mock grant endpoint accepts only the seeded `demo-agent` identity. This
  represents a simple demo allowlist; it is not a real agent-registration or
  agent-verification mechanism.
- The bank API keeps token claims in memory and checks token role, expiry,
  permissions, and delegated transfer limit on requests. Only the customer
  token can view or decide that customer's pending approvals.
- This is a mock login and token issuer, not production authentication. The
  seeded password, in-memory token store, and policies are for localhost demo
  use only.
- The mock server binds to `127.0.0.1` and stores its fake balance and approvals
  in memory. Do not expose it to a network or use real credentials or accounts.
- The simulator intentionally connects only to its local mock API. It does not
  send the demo password or tokens to a real internet-banking environment.

## Run tests

```sh
python3 -m unittest discover -s tests -v
```
