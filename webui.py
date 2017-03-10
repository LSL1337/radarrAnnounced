import logging
import os
import re
from urllib.parse import urlparse

import requests
from flask import Flask
from flask import abort
from flask import render_template
from flask import request
from flask import send_file
from flask import send_from_directory
from flask_httpauth import HTTPBasicAuth

import config
import db
import radarr
import utils

logger = logging.getLogger("WEB-UI")
logger.setLevel(logging.DEBUG)

app = Flask("radarrAnnounced")
auth = HTTPBasicAuth()
cfg = config.init()
trackers = None


def run(loaded_trackers):
    global trackers
    trackers = loaded_trackers
    app.run(debug=False, host=cfg['server.host'], port=int(cfg['server.port']), use_reloader=False)


def shutdown_server():
    func = request.environ.get('werkzeug.server.shutdown')
    if func is None:
        raise RuntimeError('Not running with the Werkzeug Server')
    func()


# mitm tracker torrent route
@app.route('/mitm/<tracker>/<torrent_id>/<torrent_name>')
def serve_torrent(tracker, torrent_id, torrent_name):
    global trackers
    found_tracker = None

    logger.debug("Requested MITM: %s (%s) from tracker: %s", torrent_name, torrent_id, tracker)
    try:
        found_tracker = trackers.get_tracker(tracker)
        if found_tracker is not None:
            # ask tracker for torrent url
            download_url = found_tracker['plugin'].get_real_torrent_link(torrent_id, torrent_name)
            # ask tracker for cookies
            cookies = found_tracker['plugin'].get_cookies()

            if download_url is not None and cookies is not None:
                # download torrent
                torrent_path = utils.download_torrent(tracker, torrent_id, cookies, download_url)
                if torrent_path is not None:
                    # serve torrent
                    logger.debug("Serving torrent: %s", torrent_path)
                    return send_file(filename_or_fp=torrent_path.__str__())

    except AttributeError:
        logger.debug("Tracker was not configured correctly for MITM torrent requests! "
                     "Required methods: get_real_torrent_link() and get_cookies()")
    except Exception as ex:
        logger.exception("Unexpected exception occurred at serve_torrent:")

    return abort(404)


# panel routes
@auth.get_password
def get_pw(username):
    if not username == cfg['server.user']:
        return None
    else:
        return cfg['server.pass']
    return None


@app.route('/assets/<path:path>')
@auth.login_required
def send_asset(path):
    return send_from_directory("templates/assets/{}".format(os.path.dirname(path)), os.path.basename(path))


@app.route("/")
@auth.login_required
@db.db_session
def index():
    return render_template('index.html', snatched=db.Snatched.select().order_by(db.desc(db.Snatched.date)).limit(20),
                           announced=db.Announced.select().order_by(db.desc(db.Announced.date)).limit(20))


@app.route("/trackers", methods=['GET', 'POST'])
@auth.login_required
def trackers():
    if request.method == 'POST':
        if 'iptorrents_torrentpass' in request.form:
            cfg['iptorrents.torrent_pass'] = request.form['iptorrents_torrentpass']
            cfg['iptorrents.nick'] = request.form['iptorrents_nick']
            cfg['iptorrents.nick_pass'] = request.form['iptorrents_nickpassword']
            logger.debug("saved iptorrents settings")

        if 'ptp_torrentpass' in request.form:
            cfg['ptp.auth_key'] = request.form['ptp_authkey']
            cfg['ptp.torrent_pass'] = request.form['ptp_torrentpass']
            cfg['ptp.nick'] = request.form['ptp_nick']
            cfg['ptp.site_username'] = request.form['ptp_site_username']
            cfg['ptp.nick_pass'] = request.form['ptp_nickpassword']
            cfg['ptp.irc_key'] = request.form['ptp_irc_key']
            cfg['ptp.announcer'] = request.form['ptp_announcer']
            logger.debug("saved ptp settings")


        cfg.sync()

    return render_template('trackers.html')


@app.route("/logs")
@auth.login_required
def logs():
    logs = []
    with open('status.log') as f:
        for line in f:
            log_parts = re.search('(^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})\s-\s(\S+)\s+-\s(.+)', line)
            if log_parts:
                logs.append({'time': log_parts.group(1),
                             'tag': log_parts.group(2),
                             'msg': log_parts.group(3)})

    return render_template('logs.html', logs=logs)


@app.route("/settings", methods=['GET', 'POST'])
@auth.login_required
def settings():
    if request.method == 'POST':
        cfg['server.host'] = request.form['server_host']
        cfg['server.port'] = request.form['server_port']
        cfg['server.user'] = request.form['server_user']
        cfg['server.pass'] = request.form['server_pass']

        cfg['radarr.url'] = request.form['radarr_url']
        cfg['radarr.apikey'] = request.form['radarr_apikey']

        if 'debug_file' in request.form:
            cfg['bot.debug_file'] = True
        else:
            cfg['bot.debug_file'] = False

        if 'debug_console' in request.form:
            cfg['bot.debug_console'] = True
        else:
            cfg['bot.debug_console'] = False

        cfg.sync()
        logger.debug("Saved settings: %s", request.form)

    return render_template('settings.html')


@app.route("/radarr/check", methods=['POST'])
@auth.login_required
def check():
    try:
        data = request.json
        if 'apikey' in data and 'url' in data:
            # Check if api key is valid
            logger.debug("Checking whether apikey: %s is valid for: %s", data.get('apikey'), data.get('url'))

            headers = {'X-Api-Key': data.get('apikey')}
            resp = requests.get(url="{}/api/diskspace".format(data.get('url')), headers=headers).json()
            logger.debug("check response: %s", resp)

            if 'error' not in resp:
                return 'OK'

    except Exception as ex:
        logger.exception("Exception while checking radarr apikey:")

    return 'ERR'


@app.route("/radarr/notify", methods=['POST'])
@auth.login_required
@db.db_session
def notify():
    try:
        data = request.json
        if 'id' in data:
            # Request to check this torrent again
            announcement = db.Announced.get(id=data.get('id'))
            if announcement is not None and len(announcement.title) > 0:
                logger.debug("Checking announcement again: %s", announcement.title)

                approved = radarr.wanted(announcement.title, announcement.torrent, announcement.indexer)
                if approved:
                    logger.debug("Radarr accepted the torrent this time!")
                    return "OK"
                else:
                    logger.debug("Radarr still refused this torrent...")
                    return "ERR"

    except Exception as ex:
        logger.exception("Exception while notifying radarr announcement:")

    return "ERR"


@app.context_processor
def inject_conf_in_all_templates():
    global cfg
    return dict(conf=cfg)


@app.context_processor
def utility_processor():
    def format_timestamp(timestamp):
        formatted = utils.human_datetime(timestamp)
        return formatted

    def correct_download(link):
        formatted = link
        if 'localhost' in link:
            parts = urlparse(request.url)
            if parts.hostname is not None:
                formatted = formatted.replace('localhost', parts.hostname)

        return formatted

    return dict(format_timestamp=format_timestamp, correct_download=correct_download)
