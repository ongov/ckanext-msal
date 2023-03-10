# CKAN
import ckan.plugins as plugins
import ckan.plugins.toolkit as toolkit
import logging
import ckan.model as model
from ckan.common import config, request, g
from flask import Blueprint, render_template, render_template_string
import ckan.lib.helpers as h

# MSAL
import msal

# Plugin
from . import msal_config

log = logging.getLogger(__name__)
application = msal.ConfidentialClientApplication(
        msal_config.CLIENT_ID,
        authority=msal_config.AUTHORITY,
        client_credential=msal_config.CLIENT_SECRET
    )


def _validate_email_domains(user):
    '''Validate user's email domain is allowed.
    Potential for guest user to be added to Azure AD that
    we don't want to be accepted in CKAN, even if added there.
    '''
    try:
        domain = user.split('@')[1].lower()
        if not domain in msal_config.EMAIL_DOMAINS:
            raise Exception(user)
    except Exception as e:
        log.error('Exception raised. Improper email domain. {}'
                  .format(repr(e)))
        return False
    return True


def _validate_user_exists_in_ckan(user, user_name):
    '''Validate the user is registered and active in CKAN.
    Return boolean.
    Checks if user exists based on username.
    Checks if user is active.
    Chekcs if user email is a complete match (just username may provide false
    match to differnet domain).
    Check state, state: deleted can still login but gets a blank page because
    CKAN is handling authorization later as well.
    '''
    try:
        user_obj = model.User.get(user_name)
        if (user_obj and
                user_obj.state == 'active' and
                user_obj.email.lower() == user.lower()):
            return True
        else:
            raise Exception(user_name)
    except (toolkit.ObjectNotFound, Exception) as e:
        log.error('Exception raised. Invalid user. {}'
                  .format(repr(e)))
        return False


def msal_login():
    '''Make call to authorization_url to authenticate user and get
    authorization code.
    '''
    authorization_url = application.get_authorization_request_url(
            msal_config.SCOPE,
            redirect_uri=msal_config.REDIRECT_URI,
            #prompt="login"
        )

    resp = toolkit.h.redirect_to(authorization_url)
    log.error(authorization_url)

    return resp


def get_a_token():
    '''Handle Azure AD callback.
    Get authorization code from Azure AD response. Use code to get
    token.
    Returns response to dashboard if logged in or aborts with 403.
    '''
    try:
        code = request.args['code']

        result = application.acquire_token_by_authorization_code(code,
                scopes=msal_config.SCOPE,
                redirect_uri=msal_config.REDIRECT_URI
            )

        user = result.get("id_token_claims", {}).get("preferred_username") # email
        user_name = user.lower().replace('.', '_').split('@')[0].strip() # ckan'd username
        # as of CKAN 2.9.6, user model is called by id not name, so need
        # to define user_id
        user_id = model.User.get(user_name).id
        # CKAN 2.9.6 expects a serial_counter to be passed into environ
        # Hard-code counter to '1' here
        user_id = '{},{}'.format( user_id, 1)

        # Validate user info.
        if not _validate_email_domains(user):
            raise Exception(user)
        if not _validate_user_exists_in_ckan(user, user_name):
            raise Exception(user)

        # Note: If developing locally make sure the site_url is set to http://localhost
        #       and not 127.0.0.1 otherwise it will log the user out.
        resp = toolkit.h.redirect_to('/dashboard')

        # Set the repoze.who cookie to match a given user_id
        if u'repoze.who.plugins' in request.environ:
            rememberer = request.environ[u'repoze.who.plugins'][u'friendlyform']
            # pass user_id and not user_name to identity to be compatible with
            # breaking changes introduced in ckan 2.9.6
            identity = {u'repoze.who.userid': user_id}
            resp.headers.extend(rememberer.remember(request.environ, identity))
    except Exception as e:
        log.error('Exception raised. Unable to authenticate user. {}'
                  .format(repr(e)))
        toolkit.abort(403, 'Not authorized.')

    return resp

def _get_repoze_handler(handler_name):
    u'''Returns the URL that repoze.who will respond to and perform a
    login or logout.'''
    return getattr(request.environ[u'repoze.who.plugins'][u'friendlyform'],
                   handler_name)

def override_logged_out():
    '''Override the logged_out() function to call Microsoft account logout.
    The IAuthenticator logout() did not seem to properly follow redirect 
    (I think this is because it is an interactive page it goes to or because 
    this is flask and there ins't a global response object like in pylons) and
    continued on with the standard logout() logic.
    Also, doing it at logged_out() vs logged_out_page() to prevent "came_from" param going to a
    different page and stopping the logout below.
    '''
    h.flash_success('You are now logged out.')
    return toolkit.h.redirect_to('https://login.microsoftonline.com/organizations/oauth2/v2.0/logout?post_logout_redirect_uri={}/user/login'.format(config.get('ckan.site_url')))



class MsalPlugin(plugins.SingletonPlugin):
    plugins.implements(plugins.IConfigurer)
    plugins.implements(plugins.IBlueprint)
    plugins.implements(plugins.IAuthenticator)


    # IConfigurer

    def update_config(self, config_):
        toolkit.add_template_directory(config_, 'templates')
        toolkit.add_public_directory(config_, 'public')
        toolkit.add_resource('fanstatic', 'msal')


    # IAuthenticator

    def login(self):
        pass

    def identify(self):
        pass

    def abort(self, status_code, detail, headers, comment):
        pass

    def logout(self):
        log.error('logout')
        url = toolkit.h.url_for(u'user.logged_out_page')
        log.error(url)
        log.error(_get_repoze_handler(u'logout_handler_path'))
        redirect = _get_repoze_handler(u'logout_handler_path') + u'?came_from=' + url
        log.error(redirect)
        log.error(msal_config.AUTHORITY + "/oauth2/v2.0/logout" +
            "?post_logout_redirect_uri=https://test.data.ontario.ca" + redirect)
        #toolkit.h.redirect_to(msal_config.AUTHORITY + "/oauth2/v2.0/logout" + "?post_logout_redirect_uri=https://test.data.ontario.ca/user/_logout")
        return h.redirect_to('/')
        #toolkit.h.redirect_to('/dataset')
        #log.error(resp.__dict__)
        #return resp

    # IBlueprint

    def get_blueprint(self):
        blueprint = Blueprint(self.name, self.__module__)
        rules = [
            ('/msal/login', 'msal_login', msal_login),
            ('/getAToken', 'get_a_token', get_a_token),
            ('/user/logged_out', 'logged_out', override_logged_out)
        ]

        for rule in rules:
            blueprint.add_url_rule(*rule)

        return blueprint

