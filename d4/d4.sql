CREATE TABLE rule_table AS
SELECT
	f.flight_no AS flight_number,
	bp.seat_no AS seat_number,
	CASE
    	WHEN tf.amount = MAX(tf.amount) OVER (PARTITION BY f.flight_no, bp.seat_no, tf.fare_conditions)
			AND COUNT(tf.amount) OVER (PARTITION BY f.flight_no, bp.seat_no, tf.fare_conditions) = 2
		THEN CONCAT(tf.fare_conditions, ' expensive')
    	ELSE tf.fare_conditions
  	END AS fare_conditions,
	tf.amount
FROM ticket_flights tf
JOIN boarding_passes bp ON tf.ticket_no = bp.ticket_no
JOIN flights f ON f.flight_id = tf.flight_id
GROUP BY f.flight_no, bp.seat_no, tf.fare_conditions, tf.amount;