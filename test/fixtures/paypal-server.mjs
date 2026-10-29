/** Explicit local protocol fixture. No provider account or actual payment. */
import { createServer } from "node:http";
export async function paypalFixture() {
  const orders = new Map(),
    keys = new Map();
  let captured = 0,
    refunds = 0;
  const server = createServer(async (req, res) => {
    let raw = "";
    for await (const chunk of req) raw += chunk;
    let body = {};
    try {
      body = JSON.parse(raw || "{}");
    } catch {}
    const path = new URL(req.url, "http://fixture").pathname;
    let value,
      status = 200;
    if (path === "/v1/oauth2/token")
      value = { access_token: "fixture-token-not-live", expires_in: 3600 };
    else if (path === "/v1/identity/oauth2/userinfo") value = {};
    else if (path === "/v1/notifications/verify-webhook-signature")
      value = {
        verification_status:
          body.transmission_sig === "fixture-valid" ? "SUCCESS" : "FAILURE",
      };
    else if (path === "/v2/checkout/orders" && req.method === "POST") {
      const key = req.headers["paypal-request-id"];
      let id = keys.get(key);
      if (!id) {
        id = "ORDER_" + (orders.size + 1);
        orders.set(id, { unit: body.purchase_units[0], captured: false });
        keys.set(key, id);
      }
      value = {
        id,
        links: [
          {
            rel: "approve",
            href: "https://www.sandbox.paypal.com/checkoutnow?token=" + id,
          },
        ],
      };
    } else {
      const orderMatch =
        /^\/v2\/checkout\/orders\/(ORDER_\d+)(\/capture)?$/.exec(path);
      const capMatch =
        /^\/v2\/payments\/captures\/(CAPTURE_\d+)(\/refund)?$/.exec(path);
      const refundMatch = /^\/v2\/payments\/refunds\/(REFUND_\d+)$/.exec(path);
      if (orderMatch) {
        const id = orderMatch[1],
          order = orders.get(id);
        if (!order) {
          status = 404;
          value = { error: "unknown fixture order" };
        } else {
          if (orderMatch[2]) {
            if (!order.captured) captured++;
            order.captured = true;
          }
          value = {
            id,
            intent: "CAPTURE",
            status: order.captured ? "COMPLETED" : "APPROVED",
            purchase_units: [
              {
                ...order.unit,
                payee: { merchant_id: "FIXTURE_MERCHANT" },
                payments: {
                  captures: order.captured
                    ? [{ id: id.replace("ORDER", "CAPTURE") }]
                    : [],
                  refunds: order.refund
                    ? [{ id: id.replace("ORDER", "REFUND") }]
                    : [],
                },
              },
            ],
          };
        }
      } else if (capMatch) {
        const id = capMatch[1],
          order = orders.get(id.replace("CAPTURE", "ORDER"));
        if (!order) {
          status = 404;
          value = { error: "unknown capture" };
        } else if (capMatch[2]) {
          if (!order.refund) refunds++;
          order.refund = body.amount;
          value = {
            id: id.replace("CAPTURE", "REFUND"),
            status: "COMPLETED",
            amount: body.amount,
          };
        } else {
          const gross = order.unit.amount;
          value = {
            id,
            status: order.refund ? "REFUNDED" : "COMPLETED",
            supplementary_data: {
              related_ids: { order_id: id.replace("CAPTURE", "ORDER") },
            },
            amount: gross,
            seller_receivable_breakdown: {
              gross_amount: gross,
              paypal_fee: { currency_code: "USD", value: "0.10" },
              net_amount: {
                currency_code: "USD",
                value: (Number(gross.value) - 0.1).toFixed(2),
              },
            },
          };
        }
      } else if (refundMatch) {
        const id = refundMatch[1],
          order = orders.get(id.replace("REFUND", "ORDER"));
        value = {
          id,
          status: "COMPLETED",
          amount: order?.refund,
          links: [
            {
              rel: "up",
              href:
                "https://api-m.sandbox.paypal.com/v2/payments/captures/" +
                id.replace("REFUND", "CAPTURE"),
            },
          ],
        };
      } else {
        status = 404;
        value = { error: "unknown fixture route" };
      }
    }
    res.writeHead(status, { "Content-Type": "application/json" });
    res.end(JSON.stringify(value));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return {
    url: "http://127.0.0.1:" + server.address().port,
    stats: () => ({ orders: orders.size, captured, refunds }),
    close: () => new Promise((resolve) => server.close(resolve)),
  };
}
