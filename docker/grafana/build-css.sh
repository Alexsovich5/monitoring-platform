#!/bin/sh
# Compile Grafana 1.9.1's LESS into src/css/*.min.css and concatenate the
# dark and light bundles in the order of tasks/options/concat.js.
# Usage: build-css.sh LESSC  (run from Grafana's src/ directory)
set -e
LESSC=$1
PATHS=vendor/bootstrap/less:css/less

"$LESSC" --yui-compress --include-path=$PATHS \
    css/less/bootstrap.dark.less css/bootstrap.dark.min.css
"$LESSC" --yui-compress --include-path=$PATHS \
    css/less/bootstrap.light.less css/bootstrap.light.min.css
"$LESSC" --yui-compress --include-path=$PATHS \
    css/less/grafana-responsive.less css/bootstrap-responsive.min.css

# grunt-contrib-concat joins files with a single linefeed.
concat() {
    out=$1
    shift
    first=1
    for file in "$@"; do
        if [ $first -eq 0 ]; then printf '\n'; fi
        cat "$file"
        first=0
    done > "$out"
}

for theme in dark light; do
    concat css/grafana.$theme.min.css \
        vendor/css/normalize.min.css \
        vendor/css/timepicker.css \
        vendor/css/spectrum.css \
        vendor/css/animate.min.css \
        css/bootstrap.$theme.min.css \
        css/bootstrap-responsive.min.css \
        vendor/css/font-awesome.min.css
    test -s css/grafana.$theme.min.css
done
