<?php
// Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
// The webapp's own unit rules, run on a fixture, for tests/test_units.py: reads {"rooms":[...],"zones":[...]} on stdin and
// prints app_units_from_lists() as JSON. CONTROL_API = the ArtiLED_Control_API checkout. Nothing here is a copy of it.
$root = getenv('CONTROL_API');
ob_start();
require_once $root . '/app/includes/units.php';
ob_end_clean();
$in = json_decode(stream_get_contents(STDIN), true);
echo json_encode(app_units_from_lists($in['rooms'], $in['zones']));
