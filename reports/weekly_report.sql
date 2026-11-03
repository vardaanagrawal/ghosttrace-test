-- Sent to finance every Monday
SELECT customer_email, SUM(total_cents) FROM orders GROUP BY customer_email;
