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
    @staticmethod
    def get():
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
    @staticmethod
    def get():
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
    @staticmethod
    def get(city):
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
    @staticmethod
    def get(airport):
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
    def post(self):
        json = request.json


        passenger_name = json['passenger_name']
        passenger_id = json['passenger_id']
        flight_ids = json['flight_ids']
        fare_conditions = json['fare_conditions']

        cur = get_db().cursor()
        #сейчас допишу эту часть кода


def get_free_seat(all_seats, taken_seats):
    for s in all_seats:
        if s not in taken_seats:
            return s
    return make_response(jsonify({'error': 'No seats are available for this flight'}), 404)


class CheckIn(Resource):
    @staticmethod
    def post():
        json = request.json
        ticket_no = json["ticket_no"]
        flight_id = json["flight_id"]
        print(json["ticket_no"])
        print(json["flight_id"])
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
        print(fare_cond)

        cur.execute("""SELECT seat_no from boarding_passes WHERE flight_id = %s""", [flight_id])
        taken_seats = list(map(lambda a: a[0], cur.fetchall()))

        cur.execute("""
        SELECT s.seat_no from flights f
JOIN seats s on f.aircraft_code = s.aircraft_code
WHERE f.flight_id = %s AND fare_conditions = %s""",
                    [flight_id, fare_cond])
        all_seats = list(map(lambda a: a[0], cur.fetchall()))

        seat_no = get_free_seat(all_seats, taken_seats)
        print(seat_no)

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

        print(boarding_no)

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
