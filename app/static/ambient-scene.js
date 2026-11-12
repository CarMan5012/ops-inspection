/* Original WebGL topology scene. No external runtime assets or libraries. */
(() => {
  const root = document.querySelector('.ambient-scene');
  const canvas = root?.querySelector('canvas');
  if (!canvas) return;

  const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const narrow = window.matchMedia('(max-width: 700px)');
  const expressive = document.body.classList.contains('ambient-auth');
  let gl;
  try {
    gl = canvas.getContext('webgl', {
      alpha: true, antialias: false, depth: false,
      powerPreference: 'low-power', preserveDrawingBuffer: false,
    });
  } catch (_) { /* The SVG remains visible. */ }
  if (!gl) return;

  const vertex = `
    attribute vec3 aPosition;
    attribute vec4 aColor;
    attribute float aSize;
    uniform float uYaw;
    uniform float uPitch;
    uniform float uAspect;
    uniform float uMobile;
    uniform float uDpr;
    varying vec4 vColor;
    void main() {
      vec3 p = aPosition;
      p.x *= mix(1.0, 0.39, uMobile);
      p.y *= mix(1.0, 0.78, uMobile);
      float cy = cos(uYaw), sy = sin(uYaw);
      float cp = cos(uPitch), sp = sin(uPitch);
      vec3 turn = vec3(cy * p.x + sy * p.z, p.y, -sy * p.x + cy * p.z);
      vec3 tilt = vec3(turn.x, cp * turn.y - sp * turn.z, sp * turn.y + cp * turn.z);
      float depth = 18.0 - tilt.z;
      gl_Position = vec4(tilt.x / (0.34 * uAspect), tilt.y / 0.34, 0.0, depth);
      gl_PointSize = clamp(aSize * 18.0 / depth * uDpr, 1.0, 100.0);
      vColor = aColor;
    }
  `;
  const lineFragment = `
    precision mediump float;
    varying vec4 vColor;
    void main() { gl_FragColor = vColor; }
  `;
  const pointFragment = `
    precision mediump float;
    varying vec4 vColor;
    void main() {
      float radius = length(gl_PointCoord - vec2(0.5)) * 2.0;
      if (radius > 1.0) discard;
      float halo = pow(1.0 - radius, 2.4);
      float core = 1.0 - smoothstep(0.08, 0.34, radius);
      gl_FragColor = vec4(vColor.rgb, vColor.a * (0.28 * halo + 0.72 * core));
    }
  `;

  function makeProgram(fragmentSource) {
    const compile = (type, source) => {
      const shader = gl.createShader(type);
      gl.shaderSource(shader, source);
      gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader));
      return shader;
    };
    const program = gl.createProgram();
    gl.attachShader(program, compile(gl.VERTEX_SHADER, vertex));
    gl.attachShader(program, compile(gl.FRAGMENT_SHADER, fragmentSource));
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program));
    return {
      program,
      position: gl.getAttribLocation(program, 'aPosition'),
      color: gl.getAttribLocation(program, 'aColor'),
      size: gl.getAttribLocation(program, 'aSize'),
      yaw: gl.getUniformLocation(program, 'uYaw'),
      pitch: gl.getUniformLocation(program, 'uPitch'),
      aspect: gl.getUniformLocation(program, 'uAspect'),
      mobile: gl.getUniformLocation(program, 'uMobile'),
      dpr: gl.getUniformLocation(program, 'uDpr'),
    };
  }

  let linesProgram, pointsProgram;
  try {
    linesProgram = makeProgram(lineFragment);
    pointsProgram = makeProgram(pointFragment);
  } catch (_) { return; }

  const nodes = [];
  const edges = [];
  const blue = [0.20, 0.44, 0.60];
  const cyan = [0.15, 0.57, 0.64];
  const lime = [0.66, 0.78, 0.13];
  function node(x, y, z, size = 13, color = cyan, alpha = .82) {
    nodes.push({ p: [x, y, z], size, color, alpha });
    return nodes.length - 1;
  }
  function link(a, b, alpha = .26, color = cyan, curved = false) {
    edges.push({ a, b, alpha, color, curved });
  }
  function cluster(center, offsets, tint) {
    const hub = node(...center, 39, tint, .98);
    const ids = offsets.map(([x, y, z, size], index) =>
      node(center[0] + x, center[1] + y, center[2] + z,
        size || 13, index % 6 === 3 ? lime : tint, index % 6 === 3 ? .9 : .76));
    ids.forEach((id, index) => {
      if (index % 2 === 0 || index < 3) link(hub, id, .30, tint);
      if (index > 0) link(ids[index - 1], id, .20, tint);
    });
    link(ids[0], ids[4], .17, tint);
    link(ids[2], ids[6], .17, tint);
    return { hub, ids };
  }

  const left = cluster([-5.2, .45, -.5], [
    [-2.0, 1.9, -1.6, 12], [-1.1, 2.7, .4, 10], [.2, 2.0, -2.2, 11],
    [1.8, 2.8, -.8, 12], [2.4, 1.2, -2.9, 10], [1.4, -.7, 1.2, 14],
    [.1, -1.7, -2.4, 10], [-1.6, -2.0, .7, 13], [-2.8, -.7, -3.2, 10],
    [-3.1, .9, .3, 11], [-.7, .6, -4.5, 8],
  ], blue);
  const right = cluster([5.0, 1.15, 1.7], [
    [-2.1, 1.4, -3.5, 10], [-1.1, 2.4, -.3, 12], [.4, 2.7, -2.6, 10],
    [1.7, 1.7, .5, 13], [2.8, 2.2, -3.9, 9], [3.2, .3, -1.0, 12],
    [2.1, -.8, 1.4, 14], [.6, -1.3, -3.7, 11], [-1.0, -1.2, .2, 14],
    [-2.8, -.4, -2.5, 10], [-.3, .4, -5.0, 8], [1.6, .5, -5.1, 8],
  ], cyan);
  const lower = cluster([3.5, -3.5, -2.0], [
    [-2.2, -.1, -2.2, 10], [-1.0, .7, .2, 12], [.5, .4, -3.3, 9],
    [2.1, .7, .6, 12], [3.1, -.4, -2.1, 10], [1.3, -1.0, .4, 11],
    [-.6, -1.1, -3.1, 9],
  ], blue);
  const relayA = node(-1.2, 1.55, -4.3, 12, cyan, .58);
  const relayB = node(1.5, 1.0, -3.8, 12, cyan, .62);
  link(left.ids[3], relayA, .19, blue, true);
  link(relayA, relayB, .22, cyan, true);
  link(relayB, right.ids[0], .22, cyan, true);
  link(left.ids[5], relayA, .13, blue, true);
  link(right.ids[7], lower.ids[1], .28, cyan, true);
  link(right.ids[6], lower.ids[3], .21, cyan, true);
  link(relayB, lower.ids[0], .15, blue, true);

  const lineData = [];
  const nodeData = [];
  const glowData = [];
  function push(data, p, color, alpha, size) {
    data.push(p[0], p[1], p[2], color[0], color[1], color[2], alpha, size);
  }
  function routePoint(edge, t) {
    const a = nodes[edge.a].p, b = nodes[edge.b].p;
    const bend = edge.curved ? Math.min(1.2, Math.abs(b[0] - a[0]) * .12) : 0;
    return [
      a[0] + (b[0] - a[0]) * t,
      a[1] + (b[1] - a[1]) * t + bend * 4 * t * (1 - t),
      a[2] + (b[2] - a[2]) * t - bend * 2 * t * (1 - t),
    ];
  }
  edges.forEach(edge => {
    const steps = edge.curved ? 12 : 1;
    for (let i = 0; i < steps; i++) {
      push(lineData, routePoint(edge, i / steps), edge.color, edge.alpha, 1);
      push(lineData, routePoint(edge, (i + 1) / steps), edge.color, edge.alpha, 1);
    }
  });
  nodes.forEach(({ p, size, color, alpha }) => {
    push(glowData, p, color, alpha * .23, size * 3.4);
    push(nodeData, p, color, alpha, size);
  });

  function buffer(data, dynamic = false) {
    const result = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, result);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(data), dynamic ? gl.DYNAMIC_DRAW : gl.STATIC_DRAW);
    return { handle: result, count: data.length / 8 };
  }
  const lineBuffer = buffer(lineData);
  const glowBuffer = buffer(glowData);
  const nodeBuffer = buffer(nodeData);
  const mobileKeep = new Set([
    left.hub, ...left.ids.slice(0, 8), right.hub, ...right.ids.slice(0, 9),
    lower.hub, ...lower.ids.slice(0, 4), relayA, relayB,
  ]);
  const mobileLines = [];
  edges.filter(edge => mobileKeep.has(edge.a) && mobileKeep.has(edge.b)).forEach(edge => {
    const steps = edge.curved ? 8 : 1;
    for (let i = 0; i < steps; i++) {
      push(mobileLines, routePoint(edge, i / steps), edge.color, edge.alpha, 1);
      push(mobileLines, routePoint(edge, (i + 1) / steps), edge.color, edge.alpha, 1);
    }
  });
  const mobileGlows = [], mobileNodes = [];
  nodes.forEach(({ p, size, color, alpha }, index) => {
    if (!mobileKeep.has(index)) return;
    push(mobileGlows, p, color, alpha * .23, size * 3.4);
    push(mobileNodes, p, color, alpha, size);
  });
  const mobileLineBuffer = buffer(mobileLines);
  const mobileGlowBuffer = buffer(mobileGlows);
  const mobileNodeBuffer = buffer(mobileNodes);
  const pulseEdges = [edges[2], edges[7], edges[15], edges[23], edges[31], edges[38], edges[edges.length - 5]]
    .filter(Boolean);
  const pulseBuffer = buffer(new Array(pulseEdges.length * 8).fill(0), true);

  gl.enable(gl.BLEND);
  gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
  gl.clearColor(0, 0, 0, 0);
  let raf = 0, lastFrame = 0, elapsed = 0, dpr = 1;
  const minInterval = expressive ? 33 : 50;

  function resize() {
    dpr = Math.min(window.devicePixelRatio || 1, narrow.matches ? 1 : 1.5);
    const width = Math.max(1, Math.round(canvas.clientWidth * dpr));
    const height = Math.max(1, Math.round(canvas.clientHeight * dpr));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width; canvas.height = height;
      gl.viewport(0, 0, width, height);
    }
    render();
  }
  function draw(program, source, mode, yaw, pitch) {
    gl.useProgram(program.program);
    gl.bindBuffer(gl.ARRAY_BUFFER, source.handle);
    gl.enableVertexAttribArray(program.position);
    gl.enableVertexAttribArray(program.color);
    gl.enableVertexAttribArray(program.size);
    gl.vertexAttribPointer(program.position, 3, gl.FLOAT, false, 32, 0);
    gl.vertexAttribPointer(program.color, 4, gl.FLOAT, false, 32, 12);
    gl.vertexAttribPointer(program.size, 1, gl.FLOAT, false, 32, 28);
    gl.uniform1f(program.yaw, yaw);
    gl.uniform1f(program.pitch, pitch);
    gl.uniform1f(program.aspect, canvas.width / canvas.height);
    gl.uniform1f(program.mobile, narrow.matches ? 1 : 0);
    gl.uniform1f(program.dpr, dpr);
    gl.drawArrays(mode, 0, source.count);
  }
  function render() {
    if (!canvas.width || !canvas.height || gl.isContextLost()) return;
    const yaw = .07 + Math.sin(elapsed * .00020) * .13;
    const pitch = -.07 + Math.sin(elapsed * .00015 + .8) * .055;
    gl.clear(gl.COLOR_BUFFER_BIT);
    draw(linesProgram, narrow.matches ? mobileLineBuffer : lineBuffer, gl.LINES, yaw, pitch);
    draw(pointsProgram, narrow.matches ? mobileGlowBuffer : glowBuffer, gl.POINTS, yaw, pitch);
    draw(pointsProgram, narrow.matches ? mobileNodeBuffer : nodeBuffer, gl.POINTS, yaw, pitch);
    if (!motion.matches) {
      const data = [];
      pulseEdges.forEach((edge, index) => {
        if (narrow.matches && (!mobileKeep.has(edge.a) || !mobileKeep.has(edge.b))) {
          push(data, [-100, -100, 0], lime, 0, 1);
          return;
        }
        const t = (elapsed * .00011 + index * .193) % 1;
        push(data, routePoint(edge, t), lime, .78, narrow.matches ? 14 : 18);
      });
      gl.bindBuffer(gl.ARRAY_BUFFER, pulseBuffer.handle);
      gl.bufferSubData(gl.ARRAY_BUFFER, 0, new Float32Array(data));
      draw(pointsProgram, pulseBuffer, gl.POINTS, yaw, pitch);
    }
  }
  function tick(now) {
    raf = 0;
    if (document.hidden || motion.matches) return;
    if (now - lastFrame >= (narrow.matches ? 50 : minInterval)) {
      elapsed += Math.min(now - (lastFrame || now), 100) * (expressive ? 1 : .45);
      lastFrame = now;
      render();
    }
    raf = requestAnimationFrame(tick);
  }
  function syncMotion() {
    cancelAnimationFrame(raf);
    raf = 0;
    if (document.hidden || motion.matches) {
      render();
    } else {
      lastFrame = 0;
      raf = requestAnimationFrame(tick);
    }
  }
  canvas.addEventListener('webglcontextlost', event => {
    event.preventDefault();
    cancelAnimationFrame(raf);
    root.classList.remove('is-ready');
  });
  window.addEventListener('resize', resize, { passive: true });
  document.addEventListener('visibilitychange', syncMotion);
  motion.addEventListener('change', syncMotion);
  narrow.addEventListener('change', resize);
  resize();
  root.classList.add('is-ready');
  syncMotion();
})();
