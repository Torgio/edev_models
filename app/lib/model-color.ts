/** Color determinista para modelos que no figuran en el catálogo visual. */
export function modelColor(model: string) {
  const hue = [...model].reduce((value, letter) => (value * 31 + letter.charCodeAt(0)) % 360, 0);
  return `hsl(${hue} 46% 46%)`;
}
