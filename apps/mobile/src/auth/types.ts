export type Interest = {
  type: string;
  key: string;
  score: number;
};

export type MeResponse = {
  id: string;
  email: string;
  createdAt: string;
  topInterests: Interest[];
  stats: {
    searches: number;
    productViews: number;
    sourceOpens: number;
  };
};

export type AuthResponse = {
  token: string;
  user: MeResponse;
};
