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
import random
import string
from oauth2client.client import flow_from_clientsecrets
from oauth2client.client import FlowExchangeError
import httplib2
import json
from flask import make_response
import requests
from functools import wraps


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


# Create anti-forgery state token route decorator.
@app.route('/login')
def showLogin():

    state = ''.join(random.choice(string.ascii_uppercase + string.digits)
                    for x in xrange(32))
    login_session['state'] = state
    # return "The current session state is %s" % login_session['state']
    return render_template('login.html', STATE=state)


# gconnect route decorator.
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
    gplus_id = credentials.id_token['sub']
    if result['user_id'] != gplus_id:
        response = make_response(
            json.dumps("Token's user ID doesn't match given user ID."), 401)
        response.headers['Content-Type'] = 'application/json'
        return response

    # Verify that the access token is valid for this app.
    if result['issued_to'] != CLIENT_ID:
        response = make_response(
            json.dumps("Token's client ID does not match app's."), 401)
        print "Token's client ID does not match app's."
        response.headers['Content-Type'] = 'application/json'
        return response

    stored_access_token = login_session.get('access_token')
    stored_gplus_id = login_session.get('gplus_id')
    if stored_access_token is not None and gplus_id == stored_gplus_id:
        response = make_response(json.dumps(
                  'Current user is already connected. '), 200)
        response.headers['Content-Type'] = 'application/json'
        return response

    # Store the access token in the session for later use.
    login_session['access_token'] = credentials.access_token
    login_session['gplus_id'] = gplus_id

    # Get user info
    userinfo_url = "https://www.googleapis.com/oauth2/v1/userinfo"
    params = {'access_token': credentials.access_token, 'alt': 'json'}
    answer = requests.get(userinfo_url, params=params)

    data = answer.json()

    login_session['username'] = data['name']
    login_session['picture'] = data['picture']
    login_session['email'] = data['email']
    # ADD PROVIDER TO LOGIN SESSION
    login_session['provider'] = 'google'

    # see if user exists, if it doesn't make a new one
    user_id = getUserID(data["email"])
    if not user_id:
        user_id = createUser(login_session)
    login_session['user_id'] = user_id

    output = ''
    output += '<img src="'
    output += login_session['picture']
    output += '''"style = "width: 150px;
                            height: 150px;border-radius: 75px;
                            -webkit-border-radius: 75px;
                            -moz-border-radius: 75px;">'''
    output += '<h1>Welcome, '
    output += login_session['username']
    output += '! Retrieving the latest Places of Power...</h1>'
   
    
    flash("You are now logged in as %s" % login_session['username'])
    return output


# User Helper Functions
def createUser(login_session):
    newUser = User(name=login_session['username'], email=login_session[
                   'email'], picture=login_session['picture'])
    session.add(newUser)
    session.commit()
    user = session.query(User).filter_by(email=login_session['email']).one()
    return user.id


def getUserInfo(user_id):
    user = session.query(User).filter_by(id=user_id).one()
    return user


def getUserID(email):
    try:
        user = session.query(User).filter_by(email=email).one()
        return user.id
    except:
        return None


# DISCONNECT - Revoke a current user's token and reset their login_session
@app.route('/gdisconnect')
def gdisconnect():
    # Only disconnect a connected user.
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
            json.dumps('Failed to revoke token for given user.', 400))
        response.headers['Content-Type'] = 'application/json'
        return response


# Disconnect based on provider
@app.route('/disconnect')
def disconnect():
    if 'provider' in login_session:
        if login_session['provider'] == 'google':
            gdisconnect()
            del login_session['gplus_id']
            del login_session['access_token']
        del login_session['username']
        del login_session['email']
        del login_session['picture']
        del login_session['user_id']
        del login_session['provider']
        flash("You have successfully been logged out.")
        return redirect(url_for('showPlaces'))
    else:
        flash("You were not logged in")
        return redirect(url_for('showPlaces'))


# Main page showing all Places of Power
@app.route('/')
@app.route('/place/')
def showPlaces():
    places = session.query(Place).order_by(desc(Place.date))

    if 'username' not in login_session:
        return render_template('publicplaces.html',
                               places=places)
    else:
        return render_template('places.html',
                               places=places)


# Add a new Place of Power
@app.route('/place/new/', methods=['GET', 'POST'])
@login_required
def addPlace():
    if request.method == 'POST':
        newPlace = Place(
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


# Shows details of a Place of Power
@app.route('/place/<int:place_id>/')
@app.route('/place/<int:place_id>/details/')
def showPlace(place_id):
    place = session.query(Place).filter_by(id=place_id).one()
    creator = getUserInfo(place.user_id)
    if ('username' 
        not in login_session 
        or creator.id != login_session['user_id']):
        return render_template('publicplace.html',
                               place=place,
                               creator=creator)
    else:
        return render_template('place.html',
                               place=place,
                               creator=creator)


# Delete a Place of Power
@app.route('/place/<int:place_id>/delete/', methods=['GET', 'POST'])
@login_required
def deletePlace(place_id):
    placeToDelete = session.query(
        Place).filter_by(id=place_id).one()
    if placeToDelete.user_id != login_session['user_id']:
        return '''<script>function myFunction()
                {alert('You are not authorized to delete this place.
                Please add your own Place of Power!');}
                </script><body onload='myFunction()'>'''
    if request.method == 'POST':
        session.delete(placeToDelete)
        session.commit()
        flash('%s Successfully Deleted!' % placeToDelete.name)
        session.commit()
        return redirect(url_for('showPlaces', place_id=place_id))
    else:
        return render_template('deleteplace.html',
                               place=placeToDelete)


# Edit details of a Place of Power
@app.route('/place/<int:place_id>/edit/', methods=['GET', 'POST'])
@login_required
def editPlace(place_id):
    editedPlace = session.query(
        Place).filter_by(id=place_id).one()
    if editedPlace.user_id != login_session['user_id']:
        return '''<script>function myFunction()
                {alert('You are not authorized to edit this place.
                Please add your own Place of Power!');}
                </script><body onload='myFunction()'>'''
    if request.method == 'POST':
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
            flash('Place of Power has been successfully updated!')
            return redirect(url_for('showPlaces', place_id=place_id))
    else:
        return render_template('editplace.html',
                                place_id=place_id,
                                place=editedPlace)


# JSON APIs to view Catalog Information
@app.route('/place/json')
def catalogJSON():
    places = session.query(Place).all()
    return jsonify(items=[r.serialize for r in places])