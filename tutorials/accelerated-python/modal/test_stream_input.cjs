// Run against input.js from the patched NVIDIA image. No browser required.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const context = {
  window: { scrollX: 0, scrollY: 0 },
  document: { pointerLockElement: null },
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8') + '\nthis.Input = Input;', context);
const input = Object.create(context.Input.prototype);
const messages = [];
Object.assign(input, {
  element: { offsetWidth: 0, offsetHeight: 0, videoWidth: 1432, videoHeight: 852 },
  m: null, x: 0, y: 0, buttonMask: 0, mouseRelative: false,
  send: message => messages.push(message),
});
const event = (type, x = 100, y = 50) => ({
  type, clientX: x, clientY: y, button: 0,
  preventDefault() {},
});

// Restore while hidden, then reveal without firing a video resize event.
input._windowMath();
input.element.offsetWidth = 716;
input.element.offsetHeight = 426;
input._mouseButtonMovement(event('mousemove'));
assert.equal(messages.pop(), 'm,200,100,0,0,0');

// First click after another layout change must use its current coordinates.
input.element.offsetWidth = 1432;
input.element.offsetHeight = 852;
input._mouseButtonMovement(event('mousedown', 250, 125));
assert.equal(messages.pop(), 'm,250,125,1,0,0');
input._mouseButtonMovement(event('mouseup', 250, 125));
assert.equal(messages.pop(), 'm,250,125,0,0,0');

// An initial click before the first video frame must not send NaN coordinates.
input.m = null;
input.element.videoWidth = 0;
input.element.videoHeight = 0;
input._mouseButtonMovement(event('mousedown'));
assert.equal(messages.length, 0);
input.element.videoWidth = 1432;
input.element.videoHeight = 852;
input._mouseButtonMovement(event('mousedown', 42, 24));
assert.equal(messages.pop(), 'm,42,24,1,0,0');
input._mouseButtonMovement(event('mouseup', 42, 24));
messages.pop();

// Relative-pointer mode retains the original movement scaling.
context.document.pointerLockElement = {};
input.cursorScaleFactor = 2;
input._mouseButtonMovement({ ...event('mousemove'), movementX: 3, movementY: -4 });
assert.equal(messages.pop(), 'm2,6,-8,0,0,0');
console.log('PASS: hidden/revealed iframe, first click, delayed video, relative pointer');
