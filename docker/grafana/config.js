// Grafana 1.9.1 settings for the monitoring platform.  The browser reads
// Zabbix data through the API's Graphite-compatible endpoints, published
// on host port 20750 (the api service's :5000).  Dashboards are the
// generated files in app/dashboards/; there is no dashboard storage.
define(['settings'], function(Settings) {
  "use strict";

  return new Settings({
    datasources: {
      graphite: {
        type: 'graphite',
        url: "http://localhost:20750",
        default: true
      }
    },

    search: {
      max_results: 100
    },

    default_route: '/dashboard/file/mp-linux.json',

    unsaved_changes_warning: false,

    playlist_timespan: "1m",

    admin: {
      password: ''
    },

    window_title_prefix: 'Grafana - ',

    plugins: {
      panels: [],
      dependencies: []
    }
  });
});
