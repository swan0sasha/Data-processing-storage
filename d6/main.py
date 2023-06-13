import datetime
import os
from binascii import b2a_hex

import psycopg
from flask import Flask, g, make_response, jsonify, request
from flask_restful import Resource, Api


def get_db() -> psycopg.Connection:
    if 'db' not in g:
        g.db = psycopg.connect('postgresql://postgres:sashaswan13@127.0.0.1/demo')
    return g.db


def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()


app = Flask(__name__)
app.teardown_appcontext(close_db)
api = Api(app)


class Cities(Resource):
    def get(self):
        cur = get_db().cursor()
        cur.execute('SELECT DISTINCT departure_city FROM routes')
        departure_cities = cur.fetchall()
        cur.execute('SELECT DISTINCT arrival_city FROM routes')
        arrival_cities = cur.fetchall()
        cur.close()
        return {
            'source': list(map(lambda a: a[0], departure_cities)),
            'destination': list(map(lambda a: a[0], arrival_cities)),
        }


class Airports(Resource):
    def get(self):
        cur = get_db().cursor()
        cur.execute('SELECT DISTINCT departure_airport FROM routes')
        departure_airports = cur.fetchall()
        cur.execute('SELECT DISTINCT arrival_airport FROM routes')
        arrival_airports = cur.fetchall()
        cur.close()
        return {
            'source': list(map(lambda a: a[0], departure_airports)),
            'destination': list(map(lambda a: a[0], arrival_airports)),
        }


class AirportsWithinCity(Resource):
    def get(self, city):
        cur = get_db().cursor()
        cur.execute('SELECT airport_code, airport_name FROM airports WHERE city = %s', [city])
        result = list(map(lambda a: {'code': a[0], 'name': a[1]}, cur.fetchall()))
        if len(result) == 0:
            return make_response(jsonify({'error': 'No such city'}), 404)
        return result


class InboundSchedule(Resource):
    @staticmethod
    def get(airport):
        cur = get_db().cursor()
        cur.execute(
            """SELECT DISTINCT
    routes.arrival_airport_name as airport,
    routes.departure_airport_name as origin,
    routes.flight_no,
    array_agg(f.scheduled_arrival::time) as time,
    to_char(f.scheduled_arrival, 'ID'::text)::integer AS day_of_week
FROM routes
JOIN flights f on routes.flight_no = f.flight_no
WHERE arrival_airport_name = %s
GROUP BY airport, origin, routes.flight_no, day_of_week, f.scheduled_arrival, f.scheduled_arrival::time
ORDER BY day_of_week, time, departure_airport_name
""",
            [airport])
        r = cur.fetchall()
        result = list(map(lambda a: {'origin': a[1],
                                     'flight_no': a[2],
                                     'time_of_arrival': a[3][0].strftime("%H:%M:%S"),
                                     'day_of_week': a[4]}, r))
        if len(result) == 0:
            return make_response(jsonify({'error': 'No such airport'}), 404)
        return result


class OutboundSchedule(Resource):
    def get(self, airport):
        cur = get_db().cursor()
        cur.execute(
            """SELECT DISTINCT
routes.departure_airport_name as airport,
    routes.arrival_airport_name as destination,
    routes.flight_no,
    array_agg(f.scheduled_departure::time) as time,
    to_char(f.scheduled_departure, 'ID'::text)::integer AS day_of_week
FROM routes
JOIN flights f on routes.flight_no = f.flight_no
WHERE departure_airport_name = %s
GROUP BY airport, destination, routes.flight_no, day_of_week, f.scheduled_departure, f.scheduled_departure::time
ORDER BY day_of_week, time, destination
""",
            [airport])
        r = cur.fetchall()
        result = list(map(lambda a: {'destination': a[1],
                                     'flight_no': a[2],
                                     'time_of_departure': a[3][0].strftime("%H:%M:%S"),
                                     'day_of_week': a[4]}, r))
        if len(result) == 0:
            return make_response(jsonify({'error': 'No such airport'}), 404)
        return result


class Booking(Resource):
    def put(self):
        json = request.json
        passenger_name = json['passenger_name']
        passenger_id = json['passenger_id']
        flight_ids = json['flight_ids']
        fare_conditions = json['fare_conditions']
        cur = get_db().cursor()
        book_ref = self.get_new_book_ref(cur)
        book_date = datetime.datetime(2023, 6, 13, 0, 0, 0, 0)
        ticket_flights = []
        tickets = []
        i = 1

        for flight in flight_ids:
            st, info = self.handle_one_flight(cur, flight, fare_conditions, i)
            if st >= 400:
                return make_response(jsonify({'error': info}), 404)
            ticket_flight = {"ticket_no": info["ticket_no"],
                             "flight_id": int(flight),
                             "fare_conditions": fare_conditions,
                             "amount": info["amount"]}
            ticket_flights.append(ticket_flight)
            ticket = {"ticket_no": info["ticket_no"],
                      "book_ref": book_ref,
                      "passenger_id": passenger_id,
                      "passenger_name": passenger_name}
            tickets.append(ticket)
            i += 1

        total_amount = 0
        for ticket_flight in ticket_flights:
            total_amount += ticket_flight["amount"]


        booking = {
            "book_ref": book_ref,
            "total_amount": total_amount
        }

        cur.execute("""INSERT INTO bookings VALUES (%s, %s, %s)""",
                    [booking["book_ref"], book_date,
                     booking["total_amount"]])

        for ticket in tickets:
            cur.execute("""INSERT INTO tickets VALUES (%s, %s, %s, %s)""",
                        [ticket["ticket_no"], ticket["book_ref"],
                         ticket["passenger_id"], ticket["passenger_name"]])

        for ticket_flight in ticket_flights:
            cur.execute("""INSERT INTO ticket_flights VALUES (%s, %s, %s, %s)""",
                        [ticket_flight["ticket_no"], ticket_flight["flight_id"],
                         ticket_flight["fare_conditions"], ticket_flight["amount"]])

        get_db().commit()

        return {"booking": booking,
                "tickets": tickets,
                "ticket_flights": ticket_flights}, 201

    def handle_one_flight(self, cur, flight_id, fare_conditions, i):
        cur.execute("""SELECT status from flights
WHERE status = 'Scheduled'
  AND flight_id = %s""", [flight_id])
        status = cur.fetchall()

        if len(status) == 0:
            return 405, "Flight " + flight_id + " is not available for booking"
        total_seats = self.get_total_seats_amount(cur, flight_id, fare_conditions)
        taken_seats = self.get_taken_sets_amount(cur, flight_id, fare_conditions)
        if total_seats == taken_seats:
            return 406, "Flight " + flight_id + " is already full and is not available for booking"

        amount = self.get_amount(cur, flight_id, fare_conditions)
        if amount == 0:
            return 407, "Flight " + flight_id + " can't be booked"

        return 201, {"ticket_no": self.get_new_ticket_no(cur, i), "amount": amount}

    def get_new_book_ref(self, cur):
        cur.execute("""SELECT book_ref from bookings""")
        taken = list(map(lambda a: a[0], cur.fetchall()))
        result = "00" + str(b2a_hex(os.urandom(4))[0:4])[2:6].upper()
        while result in taken:
            result = "00" + str(b2a_hex(os.urandom(4))[0:4])[2:6].upper()
        return result

    def get_new_ticket_no(self, cur, i):
        cur.execute("""SELECT ticket_no from ticket_flights ORDER BY ticket_no DESC LIMIT 1""")
        new_ticket_no = cur.fetchone()[0]
        return '000' + str(int(new_ticket_no) + i)

    def get_total_seats_amount(self, cur, flight_id, fare_conditions):
        cur.execute("""SELECT count(seat_no) from flights
JOIN seats on flights.aircraft_code = seats.aircraft_code
WHERE flight_id = %s AND fare_conditions = %s
GROUP BY flight_id, flights.aircraft_code, fare_conditions""", [flight_id, fare_conditions])
        data = cur.fetchall()
        total_seats_am = 0
        if data:
            total_seats_am = int(data.pop()[0])
        return total_seats_am

    def get_taken_sets_amount(self, cur, flight_id, fare_conditions):
        cur.execute("""SELECT count(ticket_no) from ticket_flights
WHERE flight_id = %s AND fare_conditions = %s
GROUP BY flight_id, fare_conditions""", [flight_id, fare_conditions])
        data = cur.fetchall()
        taken_sets_am = 0
        if data:
            taken_sets_am = int(data.pop()[0])
        return taken_sets_am

    def get_amount(self, cur, flight_id, fare_conditions):
        cur.execute("""SELECT pp.amount from rule_table pp
JOIN flights f on pp.flight_number = f.flight_no
WHERE f.flight_id = %s AND fare_conditions=%s
GROUP BY f.flight_id, f.flight_no, pp.fare_conditions, pp.amount
ORDER BY  pp.amount DESC """, [flight_id, fare_conditions])
        a = cur.fetchall()
        if len(a) == 0:
            return 0
        return int(a.pop()[0])


def get_free_seat(all_seats, taken_seats):
    for s in all_seats:
        if s not in taken_seats:
            return s
    return make_response(jsonify({'error': 'No seats are available for this flight'}), 404)


def find_free_seat(all_seats, taken_seats):
    for s in all_seats:
        if s not in taken_seats:
            return s
    return make_response(jsonify({'error': 'No seats are available for this flight'}), 404)


class CheckIn(Resource):
    def put(self):
        json = request.json
        ticket_no = json["ticket_no"]
        flight_id = json["flight_id"]
        cur = get_db().cursor()
        cur.execute("""
            SELECT status from flights 
            WHERE (status = 'On Time' OR flights.status = 'Delayed') AND flight_id = %s""", [flight_id])
        status = cur.fetchall()

        if len(status) == 0:
            return make_response(jsonify(
                {'error': 'Flight ' + str(flight_id) + ' is not available for registration'}), 404)

        cur.execute("""
        SELECT fare_conditions from ticket_flights 
        WHERE flight_id = %s AND ticket_no = %s""",
                    [flight_id, ticket_no])
        f = cur.fetchall()
        if len(f) == 0:
            return make_response(jsonify(
                {'error': 'No tickets with number ' + str(ticket_no) + ' were booked for flight ' + str(flight_id)}),
                405)

        fare_cond = f.pop()[0]
        cur.execute("""SELECT seat_no from boarding_passes WHERE flight_id = %s""", [flight_id])
        taken_seats = list(map(lambda a: a[0], cur.fetchall()))

        cur.execute("""
        SELECT s.seat_no from flights f
JOIN seats s on f.aircraft_code = s.aircraft_code
WHERE f.flight_id = %s AND fare_conditions = %s""",
                    [flight_id, fare_cond])
        all_seats = list(map(lambda a: a[0], cur.fetchall()))

        seat_no = find_free_seat(all_seats, taken_seats)

        cur.execute("""
        SELECT boarding_no from boarding_passes WHERE flight_id = %s
ORDER BY boarding_no DESC
LIMIT 1""",
                    [flight_id])
        boarding_no = cur.fetchall()

        if len(boarding_no) == 0:
            boarding_no = 1
        else:
            boarding_no = boarding_no.pop()[0] + 1

        cur.execute("""
                INSERT INTO boarding_passes VALUES (%s, %s, %s, %s)""",
                    [ticket_no, flight_id, boarding_no, seat_no])

        get_db().commit()

        return {"ticket_no": ticket_no,
                "flight_id": flight_id,
                "boarding_no": boarding_no,
                "seat_no": seat_no}, 201


api.add_resource(Cities, '/cities')
api.add_resource(Airports, '/airports')
api.add_resource(AirportsWithinCity, '/cities/<city>/airports')
api.add_resource(InboundSchedule, '/airports/<airport>/inbound-schedule')
api.add_resource(OutboundSchedule, '/airports/<airport>/outbound-schedule')
api.add_resource(CheckIn, '/check_in')
api.add_resource(Booking, '/booking')
app.run(debug=True)
