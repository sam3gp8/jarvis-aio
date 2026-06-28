const J = require('./house3d_core.js');
const fs = require('fs');
const theta = Number(process.argv[2] || 35);
const out = process.argv[3] || '/tmp/h3d.svg';
const floor = process.argv[4] || 'all';
const lit = { 'master bedroom':'dom', "eliana's room":'on', 'garage':'on', 'living room':'on', 'kitchen':'on', 'dining room':'on', 'guest room':'on', 'bath':'on' };
const svg = J.renderSVG({ theta, lit, floor });
fs.writeFileSync(out, svg);
console.log('wrote', out, 'theta', theta, 'floor', floor);
