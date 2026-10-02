// @vitest-environment jsdom
import {render,screen,cleanup} from '@testing-library/react';
import {afterEach,expect,test,vi} from 'vitest';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {Analytics} from './Analytics';

afterEach(()=>{cleanup();vi.unstubAllGlobals();});
test('keeps native zero and labels provider meaning without inventing missing metrics',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response(JSON.stringify([{publication_id:'publication',title:'Release',provider:'bluesky',account_name:'404 Builds',error:null,latest:{id:'snapshot',collected_at:'2026-10-01T10:00:00Z',metrics:{normalized:{likes:0},semantics:{likes:'bluesky reported likes'},provider_metrics:{likes:0}}}}]))));
  render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}><Analytics run={async action=>{await action();return true;}} busy={false}/></QueryClientProvider>);
  expect(await screen.findByRole('cell',{name:'0'})).toBeTruthy();
  expect(screen.getByRole('cell',{name:'bluesky reported likes'})).toBeTruthy();
  expect(screen.queryByRole('cell',{name:'views'})).toBeNull();
});
