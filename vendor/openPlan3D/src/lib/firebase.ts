import { initializeApp, type FirebaseApp } from 'firebase/app';
import { getAnalytics, isSupported } from 'firebase/analytics';
import { env } from '$env/dynamic/public';

// Firebase web config. These are public client identifiers rather than secrets, but
// they are per-deployment, so they come from the environment instead of the source
// tree. See .env.example. When they are absent, Firebase and analytics stay off.
const firebaseConfig = {
  apiKey: env.PUBLIC_FIREBASE_API_KEY,
  authDomain: env.PUBLIC_FIREBASE_AUTH_DOMAIN,
  projectId: env.PUBLIC_FIREBASE_PROJECT_ID,
  storageBucket: env.PUBLIC_FIREBASE_STORAGE_BUCKET,
  messagingSenderId: env.PUBLIC_FIREBASE_MESSAGING_SENDER_ID,
  appId: env.PUBLIC_FIREBASE_APP_ID,
  measurementId: env.PUBLIC_FIREBASE_MEASUREMENT_ID,
};

const configured = Boolean(
  firebaseConfig.apiKey && firebaseConfig.projectId && firebaseConfig.appId,
);

export const app: FirebaseApp | null = configured ? initializeApp(firebaseConfig) : null;

// Only init analytics in browser (not during SSR/build)
export const analytics = app
  ? isSupported().then((yes) => (yes ? getAnalytics(app) : null))
  : Promise.resolve(null);
