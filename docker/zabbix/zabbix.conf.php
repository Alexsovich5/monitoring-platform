<?php
// Frontend database settings. Shipping this file skips the setup wizard.
global $DB;

$DB['TYPE']     = 'POSTGRESQL';
$DB['SERVER']   = 'db';
$DB['PORT']     = '0';
$DB['DATABASE'] = 'zabbix';
$DB['USER']     = 'zabbix';
$DB['PASSWORD'] = 'zabbix';
$DB['SCHEMA']   = '';

$ZBX_SERVER      = 'localhost';
$ZBX_SERVER_PORT = '10051';
$ZBX_SERVER_NAME = 'monplat';

$IMAGE_FORMAT_DEFAULT = IMAGE_FORMAT_PNG;
?>
