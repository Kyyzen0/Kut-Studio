#version 440
// Kut-3D, moteur GPU (phase 2b d'ADR-0009) : sommets. Déformation par os (4 influences), sinon sommets déjà en
// espace monde. Les formes (morph targets) sont appliquées côté CPU avant le téléversement (ordre glTF).
layout(location = 0) in vec3 aPos;
layout(location = 1) in vec3 aNrm;
layout(location = 2) in vec2 aUv;
layout(location = 3) in vec4 aCol;
layout(location = 4) in vec3 aTan;
layout(location = 5) in float aTanSign;
layout(location = 6) in vec4 aJoints;   // indices d'articulation (entiers stockés en flottants)
layout(location = 7) in vec4 aWeights;

layout(location = 0) out vec3 vWorld;
layout(location = 1) out vec3 vNrm;
layout(location = 2) out vec2 vUv;
layout(location = 3) out vec4 vCol;
layout(location = 4) out vec3 vTan;
layout(location = 5) out float vSign;
layout(location = 6) out float vDepth;

layout(std140, binding = 0) uniform FrameBlock {
    mat4 viewProj;          // correction d'espace de découpe QRhi comprise
    vec4 eye;
    vec4 ambient;           // rgb = couleur x intensité cumulées
    vec4 counts;            // x = nombre de lumières
    vec4 lightA[16];        // xyz = direction VERS la lumière (w = 0) ou position (w = 1)
    vec4 lightB[16];        // rgb = couleur x intensité, w = portée (<= 0 : illimitée)
};

layout(std140, binding = 1) uniform DrawBlock {
    mat4 model;             // dessin à peau : monde du maillage ; sinon identité
    mat4 nmat;              // inverse transposée de la partie linéaire de `model`
    vec4 base;
    vec4 emissive;
    vec4 params;            // x = métal, y = rugosité, z = seuil alpha, w = mode (0 opaque, 1 mask, 2 blend)
    vec4 flags0;            // x = base, y = normales, z = métal/rugosité, w = émissive (1 = texture présente)
    vec4 flags1;            // x = occlusion, y = dessin à peau
};

layout(std140, binding = 2) uniform JointBlock {
    mat4 joints[128];       // MAX_GPU_JOINTS (kut3d/gpu/limits.py) ; la dernière est l'identité
};

vec3 safeNormalize(vec3 v) {
    float l = length(v);
    return l > 1e-10 ? v / l : vec3(0.0);
}

void main() {
    vec3 pos = aPos;
    vec3 nrm = aNrm;
    vec3 tan = aTan;
    if (flags1.y > 0.5) {
        mat4 m = aWeights.x * joints[int(aJoints.x + 0.5)]
               + aWeights.y * joints[int(aJoints.y + 0.5)]
               + aWeights.z * joints[int(aJoints.z + 0.5)]
               + aWeights.w * joints[int(aJoints.w + 0.5)];
        pos = (model * (m * vec4(aPos, 1.0))).xyz;
        nrm = safeNormalize((nmat * (m * vec4(aNrm, 0.0))).xyz);
        tan = (model * (m * vec4(aTan, 0.0))).xyz;
    }
    vWorld = pos;
    vNrm = nrm;
    vUv = aUv;
    vCol = aCol;
    vTan = tan;
    vSign = aTanSign;
    vec4 clip = viewProj * vec4(pos, 1.0);
    vDepth = clip.w;        // profondeur de VUE (w de la projection perspective)
    gl_Position = clip;
}
