#version 440
layout(location = 0) in vec3 aPos;
layout(location = 1) in vec2 aUv;
layout(location = 2) in vec3 aNrm;
layout(location = 0) out vec2 vUv;
layout(location = 1) out float vShade;
layout(std140, binding = 0) uniform Buf {
    mat4 mvp;
    mat4 nrm;
    vec4 lightAmbient; // xyz = direction vers la lumière (normalisée), w = ambiant
};
void main() {
    gl_Position = mvp * vec4(aPos, 1.0);
    vUv = aUv;
    float l = clamp(dot(normalize((nrm * vec4(aNrm, 0.0)).xyz), lightAmbient.xyz), 0.0, 1.0);
    vShade = min(lightAmbient.w + (1.0 - lightAmbient.w) * l, 1.0);
}
