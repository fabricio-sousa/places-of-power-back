#!/usr/bin/env python2.7
# Import modules and dependencies.
import os
import base64
from datetime import datetime
from flask import Flask, render_template, request
from flask import redirect, jsonify, url_for, flash, make_response
from sqlalchemy import create_engine, asc, desc, DateTime
from sqlalchemy.orm import sessionmaker, scoped_session
from db_setup import Base, User, Place
from flask import session as login_session
import random, string
from oauth2client.client import flow_from_clientsecrets
from oauth2client.client import FlowExchangeError
import httplib2
import json
from flask import make_response
import requests
from functools import wraps
from httplib import ResponseNotReady


# Create a new Flask app.
app = Flask(__name__)


# Load client_secret.json for Google Oauth.
# This allows for the absolute path to be retried for the file,
# regardless of environment.
current_dir = os.path.dirname(os.path.abspath(__file__))
client_secret = os.path.join(current_dir, 'client_secret.json')

CLIENT_ID = json.loads(
    open(client_secret, 'r').read())['web']['client_id']
APPLICATION_NAME = "project-power"


# Connect to the database.
# Create database session.
engine = create_engine('postgresql://catalog:catalog@localhost/catalog')
Base.metadata.bind = engine

DBSession = scoped_session(sessionmaker(bind=engine))
session = DBSession()

def login_required(f):
    '''Checks to see whether a user is logged in'''
    @wraps(f)
    def x(*args, **kwargs):
        if 'username' not in login_session:
            return redirect('/login')
        return f(*args, **kwargs)
    return x

# Create anti-forgery state token
@app.route('/login')
def showLogin():
    """Route to the login page and create anti-forgery state token."""

    state = ''.join(random.choice(string.ascii_uppercase + string.digits)
                    for x in range(32))
    login_session['state'] = state
    return render_template("signin.html", STATE=state)


# Connect to the Google Sign-in oAuth method.
@app.route('/gconnect', methods=['POST'])
def gconnect():
    # Validate state token
    if request.args.get('state') != login_session['state']:
        response = make_response(json.dumps('Invalid state parameter.'), 401)
        response.headers['Content-Type'] = 'application/json'
        return response
    # Obtain authorization code
    code = request.data

    try:
        # Upgrade the authorization code into a credentials object
        oauth_flow = flow_from_clientsecrets(client_secret, scope='')
        oauth_flow.redirect_uri = 'postmessage'
        credentials = oauth_flow.step2_exchange(code)
    except FlowExchangeError:
        response = make_response(
            json.dumps('Failed to upgrade the authorization code.'), 401)
        response.headers['Content-Type'] = 'application/json'
        return response
    except ResponseNotReady:
        credentials = oauth_flow.step2_exchange(code)
    # Check that the access token is valid.
    access_token = credentials.access_token
    url = ('https://www.googleapis.com/oauth2/v1/tokeninfo?access_token=%s'
           % access_token)
    h = httplib2.Http()
    result = json.loads(h.request(url, 'GET')[1])
    # If there was an error in the access token info, abort.
    if result.get('error') is not None:
        response = make_response(json.dumps(result.get('error')), 500)
        response.headers['Content-Type'] = 'application/json'
        return response

    # Verify that the access token is used for the intended user.
    google_id = credentials.id_token['sub']
    if result['user_id'] != google_id:
        response = make_response(
            json.dumps("Token's user ID doesn't match given user ID."), 401)
        response.headers['Content-Type'] = 'application/json'
        return response

    # Verify that the access token is valid for this app.
    if result['issued_to'] != CLIENT_ID:
        response = make_response(
            json.dumps("Token's client ID does not match app's."), 401)
        print("Token's client ID does not match app's.")
        response.headers['Content-Type'] = 'application/json'
        return response

    stored_access_token = login_session.get('access_token')
    stored_google_id = login_session.get('google_id')
    if stored_access_token is not None and google_id == stored_google_id:
        response = make_response(
            json.dumps('Current user is already connected.'), 200)
        response.headers['Content-Type'] = 'application/json'
        return response

    # Store the access token in the session for later use.
    login_session['access_token'] = credentials.access_token
    login_session['google_id'] = google_id

    # Get user info.
    userinfo_url = "https://www.googleapis.com/oauth2/v1/userinfo"
    params = {'access_token': credentials.access_token, 'alt': 'json'}
    answer = requests.get(userinfo_url, params=params)

    data = answer.json()

    login_session['username'] = data['name']
    login_session['picture'] = data['picture']
    login_session['email'] = data['email']

    # See if the user exists. If it doesn't, make a new one.
    user_id = get_user_id(data["email"])
    if not user_id:
        user_id = create_user(login_session)
    login_session['user_id'] = user_id

    output = ''
    output += '<h1>Welcome, '
    output += login_session['username']
    output += '!</h1>'
    output += '<img src="'
    output += login_session['picture']
    output += ' " style = "width: 300px; height: 300px;border-radius: 150px;-webkit-border-radius: 150px;-moz-border-radius: 150px;"> '
    flash("You are now logged in as %s" % login_session['username'])
    return output

# User Helper Functions


# Create new user.
def create_user(login_session):
    """Crate a new user.
    Argument:
    login_session (dict): The login session.
    """

    new_user = User(
        name=login_session['username'],
        email=login_session['email'],
        picture=login_session['picture']
    )
    session.add(new_user)
    session.commit()
    user = session.query(User).filter_by(email=login_session['email']).one()
    return user.id


def get_user_info(user_id):
    """Get user information by ID.
    Argument:
        user_id (int): The user ID.
    Returns:
        The user's details.
    """

    user = session.query(User).filter_by(id=user_id).one()
    return user


def get_user_id(email):
    """Get user ID by email.
    Argument:
        email (str) : the email of the user.
    """

    try:
        user = session.query(User).filter_by(email=email).one()
        return user.id
    except:
        return None



# Disconnect Google Account.
def gdisconnect():
    """Disconnect the Google account of the current logged-in user."""

    # Only disconnect the connected user.
    access_token = login_session.get('access_token')
    if access_token is None:
        response = make_response(
            json.dumps('Current user not connected.'), 401)
        response.headers['Content-Type'] = 'application/json'
        return response

    url = 'https://accounts.google.com/o/oauth2/revoke?token=%s' % access_token
    h = httplib2.Http()
    result = h.request(url, 'GET')[0]

    if result['status'] == '200':
        response = make_response(json.dumps('Successfully disconnected.'), 200)
        response.headers['Content-Type'] = 'application/json'
        return response
    else:
        response = make_response(
            json.dumps('Failed to revoke token for given user.'), 400)
        response.headers['Content-Type'] = 'application/json'
        return response


# Main landing page for Places of Power
@app.route('/main')
def Main():    
    return render_template('index.html')


# The Help Page route decorator.
@app.route('/help')
def showHelp():
    return render_template('help.html')


# Main page route decorator showing all Places of Power
@app.route('/')
@app.route('/place')
def showPlaces():
    places = session.query(Place).order_by(desc(Place.date))
    return render_template('places.html',
                               places=places)


# Add a new Place of Power route decorator
@app.route('/place/new', methods=['GET', 'POST'])
@login_required
def addPlace():
    if request.method == 'POST':
        newPlace = Place(
            user_name=request.form['user_name'],
            name=request.form['name'],
            description=request.form['description'],
            picture_url=request.form['picture_url'],
            lat=request.form['lat'],
            lng=request.form['lng'],
            date=datetime.now(),
            user_id=login_session['user_id'])
        session.add(newPlace)
        flash('New Details for %s Successfully Added!' % newPlace.name)
        session.commit()
        return redirect(url_for('showPlaces'))
    else:
        return render_template('newplace.html')


# Shows details of a Place of Power route decorator
@app.route('/place/<int:place_id>')
@app.route('/place/<int:place_id>/details')
def showPlace(place_id):
    place = session.query(Place).filter_by(id=place_id).one()
    creator = get_user_info(place.user_id)
    if ('username' 
        not in login_session 
        or creator.id != login_session['user_id']):
        return render_template('publicplace.html', place=place)
    else:
        return render_template('place.html', place=place)


# Delete a Place of Power route decorator
@app.route('/place/<int:place_id>/delete', methods=['GET', 'POST'])
@login_required
def deletePlace(place_id):
    placeToDelete = session.query(
        Place).filter_by(id=place_id).one()
    if login_session['user_id'] != placeToDelete.user_id:
        return render_template('unauth.html')
    if request.method == 'POST':
        session.delete(placeToDelete)
        session.commit()
        flash('%s Successfully Deleted!' % (placeToDelete.name))
        return redirect(url_for('showPlaces'))
    else:
        return render_template('deleteplace.html',
                               place=placeToDelete)


# Edit details of a Place of Power route decorator
@app.route('/place/<int:place_id>/edit', methods=['GET', 'POST'])
@login_required
def editPlace(place_id):
    editedPlace = session.query(
        Place).filter_by(id=place_id).one()
    if login_session['user_id'] != editedPlace.user_id:
        return render_template('unauth.html')
    if request.method == 'POST':
        if request.form['user_name']:
            editedPlace.user_name = request.form['user_name']
        if request.form['name']:
            editedPlace.name = request.form['name']
        if request.form['description']:
            editedPlace.description = request.form['description']
        if request.form['picture_url']:
            editedPlace.picture_url = request.form['picture_url']
        if request.form['lat']:
            editedPlace.lat = request.form['lat']
        if request.form['lng']:
            editedPlace.lng = request.form['lng']

        session.add(editedPlace)
        session.commit()
        
        flash('Place of Power has been successfully updated!')
        return redirect(url_for('showPlaces'))
    else:
        return render_template('editplace.html',
                                place_id=place_id,
                                place=editedPlace)


# The Google Maps page route decorator
@app.route('/map')
def showMap():
    return render_template('map.html')


# JSON APIs to feed Places info from database.
@app.route('/json')
def placesJSON():
    places = session.query(Place).all()
    return jsonify(places=[r.serialize for r in places])

# Disconnect based on provider
@app.route('/disconnect')
def disconnect():

    if 'username' in login_session:
        try:
            gdisconnect()
            del login_session['google_id']
            del login_session['access_token']
            del login_session['username']
            del login_session['email']
            del login_session['picture']
            del login_session['user_id']
            flash("You have successfully logged out.")
            return redirect(url_for('showMap'))
        except ResponseNotReady:
            gdisconnect()
            del login_session['google_id']
            del login_session['access_token']
            del login_session['username']
            del login_session['email']
            del login_session['picture']
            del login_session['user_id']
            flash("You have successfully logged out.")
            return redirect(url_for('showMap'))
    else:
        flash("You were not logged in!")
        return redirect(url_for('showMap'))


if __name__ == '__main__':
    app.secret_key = 'super_secret_key'
    app.debug = True
    app.run()