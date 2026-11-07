// Decode browser PNG screenshots to an RGBA digest, with strict dimensions, chunk CRC and filter validation.
import{inflateSync}from'node:zlib';import{createHash}from'node:crypto';
const table=Uint32Array.from({length:256},(_,n)=>{let c=n;for(let k=0;k<8;k++)c=c&1?0xedb88320^(c>>>1):c>>>1;return c>>>0;});
export function crc32(b){let c=0xffffffff;for(const x of b)c=table[(c^x)&255]^(c>>>8);return(c^0xffffffff)>>>0;}
export function decodePng(b){
 if(!b.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10])))throw Error('Invalid PNG signature');let width,height,channels,idat=[],ended=false;
 for(let p=8;p<b.length;){if(p+12>b.length)throw Error('Truncated PNG chunk');const n=b.readUInt32BE(p),type=b.toString('ascii',p+4,p+8),end=p+8+n;if(end+4>b.length)throw Error('PNG chunk exceeds file');if(crc32(b.subarray(p+4,end))!==b.readUInt32BE(end))throw Error('PNG CRC mismatch');const data=b.subarray(p+8,end);
  if(type==='IHDR'){if(width||n!==13)throw Error('Invalid PNG IHDR');width=data.readUInt32BE(0);height=data.readUInt32BE(4);channels={0:1,2:3,4:2,6:4}[data[9]];if(!width||!height||width*height>100000000||data[8]!==8||!channels||data[10]||data[11]||data[12])throw Error('Unsupported browser PNG encoding');}
  else if(type==='IDAT')idat.push(data);else if(type==='IEND'){if(n)throw Error('Invalid IEND');ended=true;if(end+4!==b.length)throw Error('Trailing PNG data');break;}p=end+4;
 }
 if(!ended||!idat.length||!width)throw Error('Incomplete PNG');const stride=width*channels,raw=inflateSync(Buffer.concat(idat),{maxOutputLength:(stride+1)*height});if(raw.length!==(stride+1)*height)throw Error('PNG scanline length mismatch');
 const decoded=Buffer.alloc(stride*height);const paeth=(a,b,c)=>{const p=a+b-c,pa=Math.abs(p-a),pb=Math.abs(p-b),pc=Math.abs(p-c);return pa<=pb&&pa<=pc?a:pb<=pc?b:c;};
 for(let y=0;y<height;y++){const f=raw[y*(stride+1)];if(f>4)throw Error('Invalid PNG filter');for(let x=0;x<stride;x++){const i=y*stride+x,a=x>=channels?decoded[i-channels]:0,up=y?decoded[i-stride]:0,c=y&&x>=channels?decoded[i-stride-channels]:0;decoded[i]=(raw[y*(stride+1)+x+1]+[0,a,up,Math.floor((a+up)/2),paeth(a,up,c)][f])&255;}}
 const rgba=Buffer.alloc(width*height*4);for(let p=0,i=0;p<decoded.length;p+=channels,i+=4){if(channels===4)decoded.copy(rgba,i,p,p+4);else if(channels===3){decoded.copy(rgba,i,p,p+3);rgba[i+3]=255;}else{rgba[i]=rgba[i+1]=rgba[i+2]=decoded[p];rgba[i+3]=channels===2?decoded[p+1]:255;}}
 return{width,height,rgba};
}

export function pngEvidence(b){const {width,height,rgba}=decodePng(b);return{width,height,rgbaSha256:createHash('sha256').update(rgba).digest('hex')};}
