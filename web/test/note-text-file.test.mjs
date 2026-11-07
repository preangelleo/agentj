import test from 'node:test';
import assert from 'node:assert/strict';
import {readNoteTextFile,NOTE_FILE_MAX} from '../public/js/note-text-file.js';
test('UTF-8 multiline note text survives unchanged',async()=>{
 const text='董事长\nSecond line\n';assert.equal(await readNoteTextFile(new File([text],'note.txt',{type:'text/plain'})),text);
});
for(const [label,file,why] of [
 ['extension',new File(['hello'],'note.html',{type:'text/plain'}),'type'],
 ['MIME',new File(['hello'],'note.txt',{type:'text/html'}),'type'],
 ['empty',new File([],'note.txt'),'size'],
 ['large',new File(['x'.repeat(NOTE_FILE_MAX+1)],'note.txt'),'size'],
 ['invalid UTF8',new File([new Uint8Array([0xc3,0x28])],'note.txt'),'utf8'],
 ['binary',new File(['a\0b'],'note.txt'),'utf8'],
 ['synthetic key',new File(['sk-'+ 'a'.repeat(24)],'note.txt'),'secret'],
 ['synthetic password',new File(['password=fake-test-value'],'note.txt'),'secret'],
 ['synthetic private key',new File([['-----BEGIN ', 'PRIVATE KEY-----\nFAKE'].join('')],'note.txt'),'secret']
])test('reject '+label,async()=>assert.rejects(readNoteTextFile(file),{message:why}));
